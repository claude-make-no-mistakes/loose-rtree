#ifndef WORKHORSES_RTREE_FOREST_HPP
#define WORKHORSES_RTREE_FOREST_HPP

#include "rtree_policy.hpp"
#include <memory>

namespace workhorse {

inline bool covers(Box const& outer, Box const& inner)
{
    return xlo(outer) <= xlo(inner) && ylo(outer) <= ylo(inner)
        && xhi(inner) <= xhi(outer) && yhi(inner) <= yhi(outer);
}

class RTreeForest {
public:
    using Tree = bgi::rtree<Value, bgi::dynamic_rstar>;

    RTreeForest(Box const& initial, unsigned depth, bgi::dynamic_rstar const& parameters,
                bool loose = false)
        : depth_(depth), loose_(loose), parameters_(parameters),
          x_(xlo(initial), xhi(initial), 1u << depth),
          y_(ylo(initial), yhi(initial), 1u << depth),
          cells_(((1u << (2 * (depth + 1))) - 1) / 3)
    {
        branches_.reserve(64);
        neighbors_.reserve(16);
        local_.reserve(16);
    }

    std::size_t size() const { return cells_[0].count; }
    Box const& bounds() const { return cells_[0].bounds; }

    void insert(Value const& value)
    {
        auto i = locate(value.first);
        auto& cell = cells_[i];
        if (cell.tree) {
            cell.tree->insert(value);
        } else if (cell.leaf.size() < parameters_.get_max_elements()) {
            cell.leaf.push_back(value);
        } else {
            auto tree = std::make_unique<Tree>(cell.leaf.begin(), cell.leaf.end(), parameters_);
            tree->insert(value);
            cell.tree = std::move(tree);
            std::vector<Value>().swap(cell.leaf);
        }
        for (;;) {
            auto& node = cells_[i];
            if (node.count++ == 0) node.bounds = value.first;
            else bg::expand(node.bounds, value.first);
            if (i == 0) break;
            i = (i - 1) / 4;
        }
    }

    std::size_t remove(Value const& value)
    {
        auto i = locate(value.first);
        auto& cell = cells_[i];
        if (cell.tree) {
            if (!cell.tree->remove(value)) return 0;
            if (cell.tree->empty()) cell.tree.reset();
        } else {
            auto found = std::find_if(cell.leaf.begin(), cell.leaf.end(),
                                     [&](Value const& item) { return item.second == value.second; });
            if (found == cell.leaf.end()) return 0;
            *found = std::move(cell.leaf.back());
            cell.leaf.pop_back();
        }
        for (;;) {
            --cells_[i].count;
            if (i == 0) break;
            i = (i - 1) / 4;
        }
        return 1;
    }

    void range(Box const& query, std::vector<Value>& out) const { range(0, query, false, out); }

    void nearest(Point const& query, unsigned k, std::vector<Value>& out)
    {
        k = static_cast<unsigned>((std::min)(size(), std::size_t(k)));
        if (!k) return;
        const auto x = bg::get<0>(query), y = bg::get<1>(query);
        branches_.clear();
        neighbors_.clear();
        branches_.push_back({distance(bounds(), x, y), 0});
        auto consider = [&](Value const& v) {
            const auto score = distance(v.first, x, y);
            if (neighbors_.size() == k) {
                if (score >= neighbors_.front().score) return;
                std::pop_heap(neighbors_.begin(), neighbors_.end(), closer);
                neighbors_.pop_back();
            }
            neighbors_.push_back({score, v});
            std::push_heap(neighbors_.begin(), neighbors_.end(), closer);
        };
        while (!branches_.empty()) {
            std::pop_heap(branches_.begin(), branches_.end(), farther);
            auto branch = branches_.back();
            branches_.pop_back();
            if (neighbors_.size() == k && branch.score > neighbors_.front().score) break;
            auto const& node = cells_[branch.index];
            if (node.tree) {
                local_.clear();
                node.tree->query(bgi::nearest(query, k), std::back_inserter(local_));
                for (auto const& v : local_) consider(v);
            } else {
                for (auto const& v : node.leaf) consider(v);
            }
            auto start = branch.index * 4 + 1;
            if (start >= cells_.size()) continue;
            for (std::size_t i = start; i < start + 4; ++i) {
                if (!cells_[i].count) continue;
                const auto score = distance(cells_[i].bounds, x, y);
                if (neighbors_.size() == k && score > neighbors_.front().score) continue;
                branches_.push_back({score, i});
                std::push_heap(branches_.begin(), branches_.end(), farther);
            }
        }
        for (auto const& neighbor : neighbors_) out.push_back(neighbor.value);
    }

    template<class CheckTree, class CheckValue>
    void inspect(CheckTree check_tree, CheckValue check_value) const
    {
        for (std::size_t i = 0; i < cells_.size(); ++i) {
            auto const& cell = cells_[i];
            auto count = cell.tree ? cell.tree->size() : cell.leaf.size();
            if (cell.leaf.size() > parameters_.get_max_elements() || (cell.tree && !cell.leaf.empty()))
                throw std::runtime_error("forest leaf exceeds its capacity");
            const auto visit = [&](Value const& value) {
                if (locate(value.first) != i || !covers(cell.bounds, value.first))
                    throw std::runtime_error("forest value has an invalid cell");
                check_value(i, value);
            };
            if (cell.tree) {
                check_tree(i, *cell.tree);
                for (auto const& v : *cell.tree) visit(v);
            } else {
                for (auto const& v : cell.leaf) visit(v);
            }
            auto start = i * 4 + 1;
            for (auto j = start; j < start + 4 && j < cells_.size(); ++j) {
                if (cells_[j].count && !covers(cell.bounds, cells_[j].bounds))
                    throw std::runtime_error("forest parent has an invalid bound");
                count += cells_[j].count;
            }
            if (count != cell.count) throw std::runtime_error("forest count differs from child counts");
        }
    }

private:
    struct Axis {
        double low, high, scale;
        unsigned max_bin;

        Axis(double lo, double hi, unsigned bins) : low(lo), high(hi), scale(0), max_bin(bins - 1)
        {
            double padding = (hi - lo) / 8;
            low -= padding;
            high += padding;
            if (high > low) {
                auto candidate = bins / (high - low);
                if (std::isfinite(candidate)) scale = candidate;
            }
        }

        unsigned bin(double value) const
        {
            // locate() restricts the argument to this axis's finite span.
            return (std::min)(max_bin, static_cast<unsigned>((value - low) * scale));
        }
    };

    struct Cell {
        std::size_t count = 0;
        Box bounds{};
        std::vector<Value> leaf;
        std::unique_ptr<Tree> tree;
    };

    struct Branch { double score; std::size_t index; };
    struct Neighbor { double score; Value value; };
    static bool farther(Branch const& a, Branch const& b) { return a.score > b.score; }
    static bool closer(Neighbor const& a, Neighbor const& b) { return a.score < b.score; }

    unsigned depth_;
    bool loose_;
    bgi::dynamic_rstar parameters_;
    Axis x_, y_;
    std::vector<Cell> cells_;
    std::vector<Branch> branches_;
    std::vector<Neighbor> neighbors_;
    std::vector<Value> local_;

    std::size_t locate(Box const& box) const
    {
        if (xlo(box) < x_.low || xhi(box) > x_.high || ylo(box) < y_.low || yhi(box) > y_.high)
            return 0;
        if (loose_) {
            // A box no wider than its centre's cell fits inside the doubled cell.
            // Queries use the observed subtree bounds, including any overhang.
            auto x = x_.bin(xlo(box) + (xhi(box) - xlo(box)) * .5);
            auto y = y_.bin(ylo(box) + (yhi(box) - ylo(box)) * .5);
            double span = (std::max)((xhi(box) - xlo(box)) * x_.scale,
                                     (yhi(box) - ylo(box)) * y_.scale);
            std::size_t node = 0;
            for (auto shift = depth_; shift-- > 0 && span <= (1u << shift);)
                node = node * 4 + 1 + ((x >> shift) & 1u) + 2 * ((y >> shift) & 1u);
            return node;
        }
        auto x = x_.bin(xlo(box)), y = y_.bin(ylo(box));
        auto different = (x ^ x_.bin(xhi(box))) | (y ^ y_.bin(yhi(box)));
        std::size_t node = 0;
        for (auto shift = depth_; shift-- > 0;) {
            if (different & (1u << shift)) break;
            node = node * 4 + 1 + ((x >> shift) & 1u) + 2 * ((y >> shift) & 1u);
        }
        return node;
    }

    static double distance(Box const& box, double x, double y)
    {
        auto dx = (std::max)({xlo(box) - x, x - xhi(box), 0.0});
        auto dy = (std::max)({ylo(box) - y, y - yhi(box), 0.0});
        return dx * dx + dy * dy;
    }

    void range(std::size_t index, Box const& query, bool covered, std::vector<Value>& out) const
    {
        auto const& node = cells_[index];
        if (!node.count || (!covered && !intersects(node.bounds, query))) return;
        covered = covered || covers(query, node.bounds);
        if (node.tree) {
            if (covered) node.tree->query(bgi::satisfies([](Value const&) { return true; }), std::back_inserter(out));
            else node.tree->query(bgi::intersects(query), std::back_inserter(out));
        } else if (covered) {
            out.insert(out.end(), node.leaf.begin(), node.leaf.end());
        } else {
            for (auto const& value : node.leaf) {
                if (intersects(value.first, query)) out.push_back(value);
            }
        }
        auto start = index * 4 + 1;
        if (start < cells_.size()) {
            for (std::size_t i = start; i < start + 4; ++i) range(i, query, covered, out);
        }
    }
};
}

#endif
