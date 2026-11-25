#ifndef WORKHORSES_RTREE_POLICY_HPP
#define WORKHORSES_RTREE_POLICY_HPP

#include <array>
#include <cstdint>
#include <type_traits>

// This declaration is used by the small build-time Boost visitor patch.
namespace workhorse {
inline thread_local bool prefix_split = false;
inline thread_local bool count_split_bounds = false;
inline thread_local std::uint64_t split_bound_expansions = 0;
inline thread_local std::uint64_t split_corner_calls = 0;
inline thread_local bool prune_overlap = false;
inline thread_local bool planar_overlap = false;
inline thread_local std::uint64_t overlap_possible = 0;
inline thread_local std::uint64_t overlap_tested = 0;

template <class Members, class Traverse>
bool try_redistribute(typename Members::leaf&, Traverse&,
                      typename Members::parameters_type const&, typename Members::translator_type const&);
}

#include <boost/geometry.hpp>
#include <boost/geometry/index/rtree.hpp>

#include <algorithm>
#include <cmath>
#include <limits>
#include <tuple>
#include <vector>

namespace workhorse {
namespace bg = boost::geometry;
namespace bgi = boost::geometry::index;
namespace rt = bgi::detail::rtree;
using Point = bg::model::point<double, 2, bg::cs::cartesian>;
using Box = bg::model::box<Point>;
using Value = std::pair<Box, std::uint32_t>;

inline double xlo(Box const& b) { return bg::get<bg::min_corner, 0>(b); }
inline double ylo(Box const& b) { return bg::get<bg::min_corner, 1>(b); }
inline double xhi(Box const& b) { return bg::get<bg::max_corner, 0>(b); }
inline double yhi(Box const& b) { return bg::get<bg::max_corner, 1>(b); }
inline bool intersects(Box const& a, Box const& b)
{
    return xlo(a) <= xhi(b) && xhi(a) >= xlo(b) && ylo(a) <= yhi(b) && yhi(a) >= ylo(b);
}
inline double area(Box const& b)
{
    return (xhi(b) - xlo(b)) * (yhi(b) - ylo(b));
}

enum class Policy { stock, geometry, history, hindsight };
struct PolicyState {
    Policy policy = Policy::stock;
    std::array<Box, 64> history{};
    std::size_t history_position = 0;
    std::size_t window = 32;
    Box const* next_queries = nullptr;
    std::size_t next_count = 0;
    std::uint64_t stock_splits = 0, opportunities = 0, changed = 0, bytes_written = 0;
    std::int64_t predicted_work_avoided = 0;
    double minimum_saving = 96;

    void observe(Box const& box)
    {
        if (policy == Policy::history) history[history_position++ % window] = box;
    }
};

inline thread_local PolicyState* active_policy = nullptr;

struct Piece {
    Box box{};
    std::size_t entries = 0;
};
using Layout = std::array<Piece, 3>;

template <class Iterator>
Piece piece(Iterator begin, Iterator end)
{
    Piece result{begin->first, static_cast<std::size_t>(end - begin)};
    for (++begin; begin != end; ++begin) bg::expand(result.box, begin->first);
    return result;
}

inline double query_work(Layout const& layout, Box const* queries, std::size_t count)
{
    double work = 0;
    for (Piece const& part : layout) {
        if (!part.entries) continue;
        // A child visit includes a leaf scan. Eight comparisons account for
        // fixed visit work in the pilot scoring model.
        for (std::size_t i = 0; i < count; ++i) {
            if (intersects(part.box, queries[i])) work += 8 + part.entries;
        }
    }
    return work;
}

inline double uniform_work(Layout const& layout)
{
    double work = 0;
    for (Piece const& part : layout) {
        if (part.entries) {
            // One coordinate unit gives points and zero-width boxes a cost.
            const double coverage = (1 + xhi(part.box) - xlo(part.box)) * (1 + yhi(part.box) - ylo(part.box));
            work += (8 + part.entries) * coverage;
        }
    }
    return work;
}

template <class Members, class Traverse>
bool try_redistribute(typename Members::leaf& leaf, Traverse& where,
                      typename Members::parameters_type const& parameters,
                      typename Members::translator_type const& translator)
{
    using Leaf = typename Members::leaf;
    auto* state = active_policy;
    if (!state) return false;
    auto& values = rt::elements(leaf);
    state->stock_splits++;
    state->bytes_written += values.size() * sizeof(Value);
    if (state->policy == Policy::stock) return false;

    const bool geometric = state->policy == Policy::geometry;
    const bool hindsight = state->policy == Policy::hindsight;
    const std::size_t query_count = hindsight ? state->next_count : std::min(state->window, state->history_position);
    const Box* queries = hindsight ? state->next_queries : state->history.data();
    if (!geometric && query_count < 8) return false;

    auto& siblings = where.parent_elements();
    const auto limit = parameters.get_max_elements();
    const auto minimum = parameters.get_min_elements();
    Box overflow = piece(values.begin(), values.end()).box;
    std::size_t sibling_index = siblings.size();
    double smallest_area = std::numeric_limits<double>::infinity();
    for (std::size_t i = 0; i < siblings.size(); ++i) {
        if (i == where.current_child_index) continue;
        const auto& other = rt::elements(rt::get<Leaf>(*siblings[i].second));
        if (other.empty() || values.size() + other.size() > 2 * limit) continue;
        Box joined = overflow;
        bg::expand(joined, siblings[i].first);
        double score = area(joined);
        if (score < smallest_area) {
            smallest_area = score;
            sibling_index = i;
        }
    }
    if (sibling_index == siblings.size()) return false;
    auto& other = rt::elements(rt::get<Leaf>(*siblings[sibling_index].second));
    auto overall = overflow;
    bg::expand(overall, siblings[sibling_index].first);
    if (!geometric && std::none_of(queries, queries + query_count, [&](Box const& q) { return intersects(q, overall); }))
        return false;

    // Run Boost's own split-choice code on a copy to price the normal path.
    auto split = values;
    std::size_t axis = 0, corner = 0, index = minimum;
    using Margin = typename bgi::detail::default_margin_result<Box>::type;
    using Content = typename bgi::detail::default_content_result<Box>::type;
    Margin margin = (std::numeric_limits<Margin>::max)();
    Content overlap = (std::numeric_limits<Content>::max)(), content = overlap;
    rt::rstar::choose_split_axis_and_index<Box, 2>::apply(split, axis, corner, index, margin, overlap, content,
                                                        parameters, translator);
    if (corner == bg::min_corner) {
        rt::rstar::nth_element<bg::min_corner, 2>::apply(split, parameters, axis, index, translator);
    } else {
        rt::rstar::nth_element<bg::max_corner, 2>::apply(split, parameters, axis, index, translator);
    }
    Layout stock{piece(split.begin(), split.begin() + index), piece(split.begin() + index, split.end()),
                 piece(other.begin(), other.end())};
    auto measure = [&](Layout const& layout) {
        return geometric ? uniform_work(layout) : query_work(layout, queries, query_count);
    };
    double baseline = measure(stock);
    std::vector<Value> combined(values.begin(), values.end());
    combined.insert(combined.end(), other.begin(), other.end());
    const std::size_t total = combined.size();
    const std::size_t lo = std::max(minimum, total - limit);
    const std::size_t hi = std::min(limit, total - minimum);
    if (lo > hi) return false;
    state->opportunities++;

    double best = geometric ? 0.95 * baseline : baseline - state->minimum_saving;
    std::size_t cut = 0;
    std::vector<Value> ordered;
    Layout winner{};
    for (int dimension = 0; dimension < 2; ++dimension) {
        auto key = [&](Value const& value) {
            Box const& b = value.first;
            return std::tuple{dimension == 0 ? xlo(b) + xhi(b) : ylo(b) + yhi(b), value.second};
        };
        std::sort(combined.begin(), combined.end(), [&](Value const& a, Value const& b) { return key(a) < key(b); });
        std::array<std::size_t, 3> splits{lo, (lo + hi) / 2, hi};
        const auto unique_end = std::unique(splits.begin(), splits.end());
        for (auto it = splits.begin(); it != unique_end; ++it) {
            auto midpoint = combined.begin() + *it;
            Layout alternative{piece(combined.begin(), midpoint), piece(midpoint, combined.end()), Piece{}};
            double cost = measure(alternative);
            if (cost < best) {
                best = cost;
                cut = *it;
                ordered = combined;
                winner = alternative;
            }
        }
    }
    if (!cut) return false;
    // Allocate both groups before changing either leaf. Both swaps use the
    // owning leaf's allocator and leave ancestor coverage valid.
    using Values = std::decay_t<decltype(values)>;
    Values left(ordered.begin(), ordered.begin() + cut, values.get_allocator());
    Values right(ordered.begin() + cut, ordered.end(), other.get_allocator());
    values.swap(left);
    other.swap(right);
    siblings[where.current_child_index].first = winner[0].box;
    siblings[sibling_index].first = winner[1].box;
    state->stock_splits--;
    state->changed++;
    state->bytes_written += (total - left.size()) * sizeof(Value);
    if (!geometric) state->predicted_work_avoided += static_cast<std::int64_t>(baseline - best);
    return true;
}
}

#endif
