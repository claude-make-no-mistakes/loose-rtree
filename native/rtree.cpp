#include "rtree_policy.hpp"
#include "rtree_forest.hpp"

#include <boost/geometry/index/detail/rtree/utilities/are_boxes_ok.hpp>
#include <boost/geometry/index/detail/rtree/utilities/are_counts_ok.hpp>
#include <boost/geometry/index/detail/rtree/utilities/are_levels_ok.hpp>
#include <boost/geometry/index/detail/rtree/utilities/view.hpp>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>

using namespace workhorse;
using Tree = bgi::rtree<Value, bgi::dynamic_rstar>;
using View = rt::utilities::view<Tree>;
using Clock = std::chrono::steady_clock;

struct Operation {
    char kind;
    std::uint32_t id = 0;
    Box box;
    std::size_t k = 0;
};

struct Workload {
    std::vector<Operation> operations;
    std::size_t initial;
};

static Workload read_workload(char const* path)
{
    std::ifstream file(path);
    Workload load;
    std::size_t count;
    if (!(file >> load.initial >> count) || load.initial > count) throw std::runtime_error("invalid trace header");
    load.operations.reserve(count);
    for (std::size_t i = 0; i < count; ++i) {
        Operation op{};
        double x1, y1, x2, y2;
        file >> op.kind;
        switch (op.kind) {
        case 'I':
        case 'M':
            file >> op.id;
            [[fallthrough]];
        case 'Q':
            file >> x1 >> y1 >> x2 >> y2;
            if (file && x1 <= x2 && y1 <= y2) {
                op.box = Box(Point(x1, y1), Point(x2, y2));
                break;
            }
            throw std::runtime_error("invalid operation box");
        case 'D':
            file >> op.id;
            break;
        case 'K':
            file >> x1 >> y1 >> op.k;
            op.box = Box(Point(x1, y1), Point(x1, y1));
            break;
        default:
            throw std::runtime_error("unknown trace operation");
        }
        if (!file || (i < load.initial && op.kind != 'I')) throw std::runtime_error("incomplete trace");
        load.operations.push_back(op);
    }
    return load;
}

static double distance(Box const& box, Point const& point)
{
    double x = bg::get<0>(point), y = bg::get<1>(point);
    double dx = std::max({xlo(box) - x, x - xhi(box), 0.0});
    double dy = std::max({ylo(box) - y, y - yhi(box), 0.0});
    return dx * dx + dy * dy;
}

using Active = std::unordered_map<std::uint32_t, Box>;
static void verify_query(Operation const& op, std::vector<Value> const& values, Active const& active)
{
    std::unordered_set<std::uint32_t> seen;
    for (auto const& [box, id] : values) {
        auto found = active.find(id);
        if (found == active.end() || !bg::equals(box, found->second) || !seen.insert(id).second)
            throw std::runtime_error("invalid or repeated query result");
    }
    if (op.kind == 'Q') {
        for (auto const& [id, box] : active) {
            if (intersects(box, op.box) != (seen.count(id) != 0)) throw std::runtime_error("incorrect range query");
        }
    } else {
        std::vector<double> expected, obtained;
        for (auto const& item : active) expected.push_back(distance(item.second, op.box.min_corner()));
        for (auto const& item : values) obtained.push_back(distance(item.first, op.box.min_corner()));
        std::sort(obtained.begin(), obtained.end());
        auto count = std::min(op.k, expected.size());
        std::partial_sort(expected.begin(), expected.begin() + count, expected.end());
        expected.resize(count);
        // Any boundary-tied ID is valid. Every result must be live and distinct.
        if (expected != obtained) throw std::runtime_error("incorrect nearest distances");
    }
}

struct Visits : View::members_holder::visitor_const {
    Box const& box;
    std::uint64_t nodes = 0, entry_tests = 0;
    explicit Visits(Box const& query) : box(query) {}
    void operator()(View::members_holder::leaf const& leaf)
    {
        ++nodes;
        entry_tests += rt::elements(leaf).size();
    }
    void operator()(View::members_holder::internal_node const& node)
    {
        ++nodes;
        for (auto const& [bounds, child] : rt::elements(node)) {
            ++entry_tests;
            if (intersects(bounds, box)) rt::apply_visitor(*this, *child);
        }
    }
};

struct CoveredRange : View::members_holder::visitor_const {
    Box const& query;
    std::vector<Value>& values;
    bool covered = false;

    CoveredRange(Box const& bounds, std::vector<Value>& output) : query(bounds), values(output) {}

    void operator()(View::members_holder::leaf const& leaf)
    {
        auto const& entries = rt::elements(leaf);
        if (covered) {
            values.insert(values.end(), entries.begin(), entries.end());
            return;
        }
        for (Value const& entry : entries) {
            if (intersects(entry.first, query)) values.push_back(entry);
        }
    }

    void operator()(View::members_holder::internal_node const& node)
    {
        bool const parent_covered = covered;
        for (auto const& [bounds, child] : rt::elements(node)) {
            if (parent_covered || intersects(bounds, query)) {
                covered = parent_covered || (xlo(query) <= xlo(bounds) && ylo(query) <= ylo(bounds)
                                              && xhi(bounds) <= xhi(query) && yhi(bounds) <= yhi(query));
                rt::apply_visitor(*this, *child);
            }
        }
        covered = parent_covered;
    }
};

struct Occupancy : View::members_holder::visitor_const {
    std::size_t minimum, maximum, level = 0;
    std::string problem;
    Occupancy(std::size_t min_count, std::size_t max_count) : minimum(min_count), maximum(max_count) {}
    void check(std::size_t n, char const* type)
    {
        if (problem.empty() && (n > maximum || (level && n < minimum))) {
            problem = std::string(type) + " at depth " + std::to_string(level) + " with " +
                      std::to_string(n) + " entries, configured " + std::to_string(minimum) +
                      ".." + std::to_string(maximum);
        }
    }
    void operator()(View::members_holder::leaf const& leaf) { check(rt::elements(leaf).size(), "leaf"); }
    void operator()(View::members_holder::internal_node const& node)
    {
        check(rt::elements(node).size(), "internal");
        ++level;
        for (auto const& child : rt::elements(node)) {
            if (!problem.empty()) break;
            rt::apply_visitor(*this, *child.second);
        }
        --level;
    }
};

struct Signature {
    std::uint64_t bits = 14695981039346656037ull;
    void add(std::uint64_t value)
    {
        bits ^= value;
        bits *= 1099511628211ull;
    }
    void number(double value)
    {
        std::uint64_t word;
        std::memcpy(&word, &value, sizeof(word));
        add(word);
    }
    void bounds(Box const& box)
    {
        number(xlo(box));
        number(ylo(box));
        number(xhi(box));
        number(yhi(box));
    }
};

struct LayoutSignature : View::members_holder::visitor_const {
    Signature& digest;
    explicit LayoutSignature(Signature& value) : digest(value) {}
    void operator()(View::members_holder::leaf const& leaf)
    {
        auto const& values = rt::elements(leaf);
        digest.add(71);
        digest.add(values.size());
        for (auto const& [box, id] : values) {
            digest.bounds(box);
            digest.add(id);
        }
    }
    void operator()(View::members_holder::internal_node const& node)
    {
        auto const& elements = rt::elements(node);
        digest.add(59);
        digest.add(elements.size());
        for (auto const& [box, child] : elements) {
            digest.bounds(box);
            rt::apply_visitor(*this, *child);
        }
    }
};

static double tail(std::vector<double> times, double quantile)
{
    if (times.empty()) return 0;
    auto index = static_cast<std::size_t>(std::ceil(times.size() * quantile)) - 1;
    std::nth_element(times.begin(), times.begin() + index, times.end());
    return times[index];
}

static void verify_tree(Tree const& tree, std::size_t position, LayoutSignature& signature)
{
    // The Boost helper visitors retain this parameters object by reference.
    auto const parameters = tree.parameters();
    using Holder = View::members_holder;
    View view(tree);
    const auto translator = view.translator();
    rt::utilities::visitors::are_boxes_ok<Holder> boxes(parameters, translator, false);
    rt::utilities::visitors::are_counts_ok<Holder> counts(parameters);
    view.apply_visitor(boxes);
    view.apply_visitor(counts);
    if (!counts.result) {
        Occupancy occupancy(parameters.get_min_elements(), parameters.get_max_elements());
        view.apply_visitor(occupancy);
        throw std::runtime_error("R-tree occupancy at operation " + std::to_string(position)
                                 + ": " + occupancy.problem);
    }
    if (!boxes.result || !rt::utilities::are_levels_ok(tree))
        throw std::runtime_error("R-tree bounds or levels failed at operation " + std::to_string(position));
    view.apply_visitor(signature);
}

static void replay(Workload const& load, std::string const& method, bool verify,
                   std::size_t window, double saving, unsigned capacity, std::size_t stride)
{
    PolicyState params;
    params.window = window;
    params.minimum_saving = saving;
    unsigned reinsertions = 0;
    unsigned depth = 0;
    bool const loose = method.compare(0, 6, "loose-") == 0;
    auto const prefix = loose ? 6u : 5u;
    if ((loose || method.compare(0, 5, "grid-") == 0) && method.size() == prefix + 3
        && method[prefix + 1] == '-' && method[prefix] >= '3' && method[prefix] <= '6'
        && (method.back() == '0' || method.back() == '9'))
        depth = method[prefix] - '0';
    bool const requested_planar = method == "planar-0" || method == "planar-9" || depth != 0;
    if (method == "stock-9" || method == "prefix-9" || method == "pruned-9" || method == "planar-9"
        || (depth && method.back() == '9'))
        reinsertions = 3 * capacity / 10;
    else if (method == "stock-3") reinsertions = 3;
    else if (method == "geometry") params.policy = Policy::geometry;
    else if (method == "history") params.policy = Policy::history;
    else if (method == "hindsight") params.policy = Policy::hindsight;
    else if (method != "stock-0" && method != "prefix-0" && method != "pruned-0" && method != "planar-0" && !depth)
        throw std::runtime_error("unknown R-tree method");
    bool const requested_pruning = method == "pruned-0" || method == "pruned-9" || requested_planar;
    prefix_split = requested_pruning || method == "prefix-0" || method == "prefix-9";
    // Leave enough FP64 headroom for widths, areas and their largest possible
    // sum in the pinned Cartesian overlap calculation.
    prune_overlap = requested_pruning && std::all_of(load.operations.begin(), load.operations.end(), [](Operation const& op) {
        if (op.kind != 'I' && op.kind != 'M') return true;
        constexpr double limit = 1e150;
        return std::max({std::abs(xlo(op.box)), std::abs(ylo(op.box)),
                         std::abs(xhi(op.box)), std::abs(yhi(op.box))}) <= limit;
    });
    planar_overlap = requested_planar && prune_overlap;
    count_split_bounds = verify;
    split_bound_expansions = split_corner_calls = 0;
    overlap_possible = overlap_tested = 0;
    unsigned minimum = (3 * capacity + 9) / 10;
    Tree tree(bgi::dynamic_rstar(capacity, minimum, reinsertions, 32));
    Active active;
    std::vector<std::size_t> future_positions;
    std::vector<Box> future_queries;
    if (params.policy == Policy::hindsight) {
        for (std::size_t i = load.initial; i < load.operations.size(); ++i) {
            if (load.operations[i].kind == 'Q') {
                future_positions.push_back(i);
                future_queries.push_back(load.operations[i].box);
            }
        }
    }
    std::vector<Value> results, final_values;
    std::vector<double> queries, updates;
    queries.reserve(load.operations.size());
    updates.reserve(load.operations.size());
    std::uint64_t visits = 0, entry_tests = 0, verified_queries = 0;
    std::uint64_t range_queries = 0, nearest_queries = 0, searched_queries = 0, build_expansions = 0;
    std::uint64_t build_overlap_possible = 0, build_overlap_tested = 0;
    Signature answers, layout;
    std::vector<std::uint32_t> ids;
    std::vector<double> distances;
    auto build_start = Clock::now();
    auto replay_start = build_start;
    std::unique_ptr<RTreeForest> forest;
    if (depth && planar_overlap && load.initial) {
        Box initial_bounds = load.operations.front().box;
        for (std::size_t i = 1; i < load.initial; ++i) bg::expand(initial_bounds, load.operations[i].box);
        forest = std::make_unique<RTreeForest>(initial_bounds, depth, tree.parameters(), loose);
    }
    const auto insert = [&](Value const& value) {
        if (forest) forest->insert(value);
        else tree.insert(value);
    };
    const auto remove = [&](Value const& value) { return forest ? forest->remove(value) : tree.remove(value); };
    for (std::size_t i = 0; i < load.operations.size(); ++i) {
        auto const& op = load.operations[i];
        const bool is_query = op.kind == 'Q' || op.kind == 'K';
        if (i == load.initial) {
            active_policy = &params;
            replay_start = Clock::now();
            build_expansions = split_bound_expansions;
            split_bound_expansions = split_corner_calls = 0;
            build_overlap_possible = overlap_possible;
            build_overlap_tested = overlap_tested;
            overlap_possible = overlap_tested = 0;
        }
        if (active_policy && params.policy == Policy::hindsight) {
            std::size_t start = std::upper_bound(future_positions.begin(), future_positions.end(), i) - future_positions.begin();
            params.next_count = std::min(params.window, future_queries.size() - start);
            params.next_queries = params.next_count ? future_queries.data() + start : nullptr;
        }
        Clock::time_point start = Clock::now();
        if (op.kind == 'I') {
            if (!active.emplace(op.id, op.box).second) throw std::runtime_error("duplicate insert");
            insert(Value{op.box, op.id});
        } else if (op.kind == 'M' || op.kind == 'D') {
            auto item = active.find(op.id);
            if (item == active.end() || remove(Value{item->second, op.id}) != 1)
                throw std::runtime_error("remove returned an unexpected count");
            if (op.kind == 'M') {
                item->second = op.box;
                insert(Value{op.box, op.id});
            } else {
                active.erase(item);
            }
        } else {
            results.clear();
            if (op.kind == 'Q') {
                if (forest) {
                    forest->range(op.box, results);
                } else if (planar_overlap) {
                    CoveredRange visitor(op.box, results);
                    View(tree).apply_visitor(visitor);
                } else {
                    tree.query(bgi::intersects(op.box), std::back_inserter(results));
                }
                params.observe(op.box);
            } else if (forest) {
                forest->nearest(op.box.min_corner(), static_cast<unsigned>(op.k), results);
            } else {
                tree.query(bgi::nearest(op.box.min_corner(), static_cast<unsigned>(op.k)), std::back_inserter(results));
            }
        }
        auto elapsed = std::chrono::duration<double, std::nano>(Clock::now() - start).count();
        if (i >= load.initial) (is_query ? queries : updates).push_back(elapsed);
        if (verify && is_query) {
            auto seen = op.kind == 'Q' ? range_queries++ : nearest_queries++;
            if (seen % stride == 0) {
                verify_query(op, results, active);
                ++verified_queries;
            }
            ++searched_queries;
            answers.add(static_cast<unsigned>(op.kind));
            answers.add(results.size());
            if (op.kind == 'Q') {
                ids.clear();
                for (auto const& value : results) ids.push_back(value.second);
                std::sort(ids.begin(), ids.end());
                for (auto id : ids) answers.add(id);
                if (!forest) {
                    Visits counter(op.box);
                    View(tree).apply_visitor(counter);
                    visits += counter.nodes;
                    entry_tests += counter.entry_tests;
                }
            } else {
                distances.clear();
                for (auto const& value : results) distances.push_back(distance(value.first, op.box.min_corner()));
                std::sort(distances.begin(), distances.end());
                for (double dist : distances) answers.number(dist);
            }
        }
        if (verify && (i + 1 == load.operations.size() || i % 1024 == 0)) {
            if ((forest ? forest->size() : tree.size()) != active.size())
                throw std::runtime_error("R-tree size differs at operation " + std::to_string(i));
            layout.add(i);
            LayoutSignature instance(layout);
            if (forest) {
                std::unordered_set<std::uint32_t> seen;
                forest->inspect(
                    [&](std::size_t cell, Tree const& local) {
                        layout.add(cell);
                        verify_tree(local, i, instance);
                    },
                    [&](std::size_t cell, Value const& v) {
                        auto found = active.find(v.second);
                        if (found == active.end() || !bg::equals(found->second, v.first) || !seen.insert(v.second).second)
                            throw std::runtime_error("forest entry differs from the live index");
                        layout.add(cell);
                        layout.add(v.second);
                        layout.bounds(v.first);
                    });
                if (seen.size() != active.size()) throw std::runtime_error("forest is missing live entries");
            } else {
                verify_tree(tree, i, instance);
            }
        }
    }
    double replay_seconds = std::chrono::duration<double>(Clock::now() - replay_start).count();
    active_policy = nullptr;
    if (verify) {
        if (forest) forest->range(forest->bounds(), final_values);
        else tree.query(bgi::satisfies([](Value const&) { return true; }), std::back_inserter(final_values));
        Operation all{'Q', 0, forest ? forest->bounds() : tree.bounds(), 0};
        verify_query(all, final_values, active);
    }
    double total = std::accumulate(queries.begin(), queries.end(), 0.0) + std::accumulate(updates.begin(), updates.end(), 0.0);
    std::cout << std::setprecision(16) << "{\"total_seconds\":" << total * 1e-9
              << ",\"phase_wall_seconds\":" << replay_seconds
              << ",\"build_seconds\":" << std::chrono::duration<double>(replay_start - build_start).count()
              << ",\"query_p95_ns\":" << tail(queries, 0.95) << ",\"update_p95_ns\":" << tail(updates, 0.95)
              << ",\"query_p99_ns\":" << tail(queries, 0.99) << ",\"update_p99_ns\":" << tail(updates, 0.99)
              << ",\"queries\":" << queries.size() << ",\"updates\":" << updates.size()
              << ",\"verified_queries\":" << verified_queries << ",\"query_signature_count\":" << searched_queries
              << ",\"query_signature\":\"" << answers.bits << "\",\"layout_signature\":\"" << layout.bits << "\""
              << ",\"split_bound_expansions\":" << split_bound_expansions
              << ",\"build_bound_expansions\":" << build_expansions << ",\"split_corner_calls\":" << split_corner_calls
              << ",\"overlap_pruning\":" << (prune_overlap ? "true" : "false")
              << ",\"forest_depth\":" << (forest ? depth : 0)
              << ",\"overlap_possible\":" << overlap_possible << ",\"overlap_tested\":" << overlap_tested
              << ",\"build_overlap_possible\":" << build_overlap_possible << ",\"build_overlap_tested\":" << build_overlap_tested
              << ",\"range_nodes\":" << visits
              << ",\"range_entry_tests\":" << entry_tests << ",\"opportunities\":" << params.opportunities
              << ",\"redistributions\":" << params.changed << ",\"stock_leaf_splits\":" << params.stock_splits
              << ",\"leaf_value_write_bytes\":" << params.bytes_written
              << ",\"projected_work_avoided\":" << params.predicted_work_avoided << "}\n";
}

int main(int argc, char** argv)
{
    try {
        if (argc != 6 && argc != 8)
            throw std::runtime_error("usage: rtree trace method timed|verify window threshold [capacity scan_stride]");
        auto window = std::stoul(argv[4]);
        auto threshold = std::stod(argv[5]);
        auto capacity = argc == 8 ? std::stoul(argv[6]) : 32;
        auto stride = argc == 8 ? std::stoul(argv[7]) : 1;
        if (window < 8 || window > 64 || !std::isfinite(threshold) || threshold < 0)
            throw std::runtime_error("invalid query sample settings");
        if (capacity < 8 || capacity > 128 || stride < 1)
            throw std::runtime_error("invalid capacity or verification stride");
        std::string mode(argv[3]);
        if (mode != "verify" && mode != "timed") throw std::runtime_error("invalid replay mode");
        replay(read_workload(argv[1]), argv[2], mode == "verify", window, threshold, static_cast<unsigned>(capacity), stride);
    } catch (std::exception const& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    return 0;
}
