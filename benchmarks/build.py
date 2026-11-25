"""Build or unpack pinned Windows UCRT64 benchmarks inside the workspace."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

from benchmarks.common import BIN, ROOT, WORK
from benchmarks.fetch import RUST, RUST_TARGET, cached_asset


HOST_TOOLCHAIN = Path(os.environ.get("WORKHORSES_UCRT64", r"C:\msys64\ucrt64"))
LIBRARIES = WORK / "packages" / "ucrt64"
RUST_HOME = WORK / "rust"
SOURCES = WORK / "sources"
NATIVE = ROOT / "native"


def run(argv, *, cwd=ROOT, env=None, capture=False):
    args = [str(arg) for arg in argv]
    if not capture:
        print(subprocess.list2cmdline(args), flush=True)
    return subprocess.run(args, cwd=cwd, env=env, check=True, text=True, capture_output=capture)


def asset(name):
    return cached_asset(name)[0]


def unpack_sources(name):
    path = asset(name)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            archive.extractall(SOURCES)
            return SOURCES / archive.namelist()[0].split("/")[0]
    with tarfile.open(path) as archive:
        result = SOURCES / archive.getnames()[0].split("/")[0]
        archive.extractall(SOURCES, filter="data")
        return result


def packages():
    target = LIBRARIES.parent
    target.mkdir(parents=True, exist_ok=True)
    contents = {
        "boost": ("ucrt64/include/boost",),
        "suitesparse": ("ucrt64/include/suitesparse", "ucrt64/lib", "ucrt64/bin"),
        "openblas": ("ucrt64/bin",),
        "metis": ("ucrt64/bin",),
        "openmp": ("ucrt64/bin",),
    }
    for name, members in contents.items():
        stamp = target / f"{name}.ready"
        if not stamp.exists():
            run(["tar.exe", "-xf", asset(f"{name}.tar.zst"), "-C", target, *members])
            stamp.touch()
    for dll in LIBRARIES.joinpath("bin").glob("*.dll"):
        shutil.copy2(dll, BIN / dll.name)
    for name in ("libgcc_s_seh-1.dll", "libgomp-1.dll", "libstdc++-6.dll", "libgfortran-5.dll",
                 "libquadmath-0.dll", "libwinpthread-1.dll", "libgmp-10.dll"):
        shutil.copy2(HOST_TOOLCHAIN / "bin" / name, BIN / name)


def gcc(*args, cxx=False):
    compiler = HOST_TOOLCHAIN / "bin" / ("g++.exe" if cxx else "gcc.exe")
    run([compiler, "-O3", "-DNDEBUG", *args])


def cuba():
    source = unpack_sources("Cuba-4.2.2.tar.gz") / "src"
    include = source / "common"
    gcc("-shared", "-DNOUNDERSCORE=1", "-I", include,
        "-I", source / "cuhre", source / "cuhre" / "Cuhre.c", include / "Global.c", include / "Data.c",
        "-Wl,--export-all-symbols", "-o", BIN / "cuhre.dll")


def delta():
    source = unpack_sources("bsdiff-4.3.tar.gz")
    for name in ("bsdiff", "bspatch"):
        path = source / f"{name}.c"
        code = path.read_text(encoding="ascii")
        anchor = "if(argc!=4)"
        if code.count(anchor) != 1:
            raise ValueError("the pinned source changed its command-line entry point")
        code = code.replace(anchor, "_set_fmode(_O_BINARY);\n\t" + anchor)
        output = SOURCES / f"{name}-windows.c"
        output.write_text(code, encoding="ascii")
        gcc("-D_FILE_OFFSET_BITS=64", "-I", NATIVE / "compat", output,
            "-Wl,-Bstatic", "-lbz2", "-Wl,-Bdynamic", "-o", BIN / f"{name}.exe")
    for name, programs in {
        "hdiffpatch.zip": ("hdiffz.exe", "hpatchz.exe"),
        "xdelta.zip": ("xdelta3.exe",),
    }.items():
        with zipfile.ZipFile(asset(name)) as archive:
            for program in programs:
                member = next(m for m in archive.namelist() if Path(m).name == program)
                (BIN / program).write_bytes(archive.read(member))


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"expected exactly one occurrence of {old!r} in the pinned upstream file")
    return source.replace(old, new)


def sparse():
    parts = []
    local = SOURCES / "amd-policy"
    local.mkdir(parents=True, exist_ok=True)
    shutil.copy2(asset("amd_internal.h"), local / "amd_internal.h")
    for filename in ("amd_order.c", "amd_1.c", "amd_2.c"):
        code = asset(filename).read_text(encoding="utf-8")
        header = '#include "amd_internal.h"'
        renames = '''
#undef AMD_order
#undef AMD_1
#undef AMD_2
#define AMD_order wh_amd_order
#define AMD_1 wh_amd_1
#define AMD_2 wh_amd_2
'''
        if filename != "amd_2.c":
            for name, upstream in (("AMD_1", "amd_1.c"), ("AMD_2", "amd_2.c")):
                declared = asset(upstream).read_text(encoding="utf-8")
                prototype = declared[declared.index(f"void {name}\n"):].split("\n{", 1)[0]
                renames += "\n" + prototype.rstrip() + ";\n"
        else:
            renames += '#include "amd_policy.h"\n'
            code = replace_once(code, "mindeg = deg ;\n\tASSERT (me", """
        me = wh_choose_pivot(me, deg, Next, n, Pe, Len, Elen, Iw, Nv);
        mindeg = deg ;
\tASSERT (me""".lstrip())
            code = replace_once(code, "if (inext != EMPTY) Last [inext] = EMPTY ;\n\tHead [deg] = inext ;", """
        if (me == Head[deg]) {
            if (inext != EMPTY) Last[inext] = EMPTY;
            Head[deg] = inext;
        } else {
            Int previous = Last[me];
            Next[previous] = inext;
            if (inext != EMPTY) Last[inext] = previous;
        }""".lstrip())
        code = replace_once(code, header, header + "\n" + renames)
        part = local / filename
        part.write_text(code, encoding="utf-8")
        parts.append(part)
    installed = LIBRARIES / "include" / "suitesparse"
    gcc("-shared", "-std=c11", "-I", NATIVE, "-I", installed, "-I", local,
        NATIVE / "sparse.c", NATIVE / "amd_policy.c", *parts,
        "-L", LIBRARIES / "lib", "-l:libamd.a", "-lcholmod", "-lcolamd", "-lsuitesparseconfig",
        "-o", BIN / "sparse.dll")
    gcc("-std=c11", ROOT / "tests" / "amd_bounds.c", "-o", BIN / "amd-bounds-test.exe")


def _spatial_overlap_header(local):
    relative = Path("boost/geometry/index/detail/rtree/rstar/choose_next_node.hpp")
    code = (LIBRARIES / "include" / relative).read_text(encoding="utf-8")
    code = replace_once(code, "#include <algorithm>", "#include <algorithm>\n#include <boost/container/static_vector.hpp>")
    code = replace_once(code, "            return choose_by_minimum_overlap_cost(children, indexable,",
                        """            if (::workhorse::planar_overlap && children.size() <= 128)
                return choose_by_minimum_overlap_cost<true>(children, indexable,
                                                            parameters.get_overlap_cost_threshold(),
                                                            index::detail::get_strategy(parameters));
            return choose_by_minimum_overlap_cost<false>(children, indexable,""")
    code = replace_once(code, """    template <typename Indexable, typename Strategy>
    static inline size_t choose_by_minimum_overlap_cost(""",
                        """    template <bool Planar, typename Indexable, typename Strategy>
    static inline size_t choose_by_minimum_overlap_cost(""")
    code = replace_once(code, """        typename rtree::container_from_elements_type<children_type, child_contents>::type
            children_contents(children_count);""",
                        """        using allocated_contents = typename rtree::container_from_elements_type<children_type, child_contents>::type;
        using stack_contents = boost::container::static_vector<child_contents, 128>;
        std::conditional_t<Planar, stack_contents, allocated_contents> children_contents(children_count);""")
    dispatch = """            chosen_index = choose_by_minimum_overlap_cost_first_n(children, indexable,
                                                                   first_n_children_count,
                                                                   children_count,
                                                                   children_contents,
                                                                   strategy);"""
    code = replace_once(code, dispatch, """            auto choose = [&](auto prune, auto count, auto planar) {
                return choose_by_minimum_overlap_cost_first_n<decltype(prune)::value, decltype(count)::value,
                                                               decltype(planar)::value>(
                    children, indexable, first_n_children_count, children_count, children_contents, strategy);
            };
            auto select = [&](auto prune, auto planar) {
                return ::workhorse::count_split_bounds
                    ? choose(prune, std::true_type{}, planar) : choose(prune, std::false_type{}, planar);
            };
            if constexpr (Planar)
                chosen_index = select(std::true_type{}, std::true_type{});
            else if (::workhorse::prune_overlap)
                chosen_index = select(std::true_type{}, std::false_type{});
            else
                chosen_index = select(std::false_type{}, std::false_type{});""")
    code = replace_once(code, "    template <typename Indexable, typename ChildrenContents, typename Strategy>",
                        "    template <bool Prune, bool Count, bool Planar, typename Indexable, typename ChildrenContents, typename Strategy>")
    code = replace_once(code, "        // for each child node\n", """        auto intersection = [&](box_type const& left, box_type const& right) {
            if constexpr (Planar) {
                auto const width = (std::min)(geometry::get<max_corner, 0>(left), geometry::get<max_corner, 0>(right)) -
                    (std::max)(geometry::get<min_corner, 0>(left), geometry::get<min_corner, 0>(right));
                auto const height = (std::min)(geometry::get<max_corner, 1>(left), geometry::get<max_corner, 1>(right)) -
                    (std::max)(geometry::get<min_corner, 1>(left), geometry::get<min_corner, 1>(right));
                return (std::max)(0.0, width) * (std::max)(0.0, height);
            } else {
                return index::detail::intersection_content(left, right, strategy);
            }
        };

        // for each child node
""")
    code = replace_once(code, "index::detail::intersection_content(box_exp, ch_j.first, strategy)",
                        "intersection(box_exp, ch_j.first)")
    anchor = "            content_type const& content_diff = children_contents[first_i].content_diff;"
    code = replace_once(code, anchor, anchor + """

            if constexpr (Count) ::workhorse::overlap_possible += children_count - 1;
            auto const improves_tie = content_diff < smallest_content_diff ||
                (content_diff == smallest_content_diff && content < smallest_content);
            if constexpr (Prune) {
                if (smallest_overlap_diff == 0 && !improves_tie) continue;
            }""")
    anchor = "                    child_type const& ch_j = children[j];"
    code = replace_once(code, anchor, anchor + "\n                    if constexpr (Count) ++::workhorse::overlap_tested;")
    anchor = "                        overlap_diff += overlap_exp - index::detail::intersection_content(ch_i.first, ch_j.first, strategy);"
    code = replace_once(code, anchor, """                        overlap_diff += overlap_exp - intersection(ch_i.first, ch_j.first);

                        if constexpr (Prune) {
                            // Enlarging a finite Cartesian box never reduces
                            // a pair's intersection area. The partial sum is
                            // a lower bound, with the original tie rule.
                            if (overlap_diff > smallest_overlap_diff ||
                                (overlap_diff == smallest_overlap_diff && !improves_tie)) break;
                        }""")
    path = local / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def spatial():
    relative = Path("boost/geometry/index/detail/rtree/visitors/insert.hpp")
    upstream = LIBRARIES / "include" / relative
    code = upstream.read_text(encoding="utf-8")
    anchor = "            split(n);"
    patched = replace_once(code, anchor, '''            if constexpr (std::is_same_v<Node, leaf>) {
                if (!m_traverse_data.current_is_root() &&
                    ::workhorse::try_redistribute<MembersHolder>(n, m_traverse_data, m_parameters, m_translator))
                    return;
            }
''' + anchor)
    local = SOURCES / "boost-policy"
    header = local / relative
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text(patched, encoding="utf-8")
    split_relative = Path("boost/geometry/index/detail/rtree/rstar/redistribute_elements.hpp")
    split = (LIBRARIES / "include" / split_relative).read_text(encoding="utf-8")
    first = split.index("        // calculate sum of margins for all distributions")
    last = split.index("        ::boost::ignore_unused(parameters);", first)
    original = split[first:last]
    score = original[original.index("            sum_of_margins +="):original.rfind("        }")]
    replacement = """        auto score_cut = [&](size_t i, Box const& box1, Box const& box2) {
""" + score + """        };
        auto const n = elements_copy.size();
        auto const cached = ::workhorse::prefix_split && n <= 129;
        if (::workhorse::count_split_bounds) {
            ::workhorse::split_corner_calls++;
            ::workhorse::split_bound_expansions += cached
                ? 2 * (n - index_first - 1)
                : (index_last - index_first) * (n - 2);
        }
        if (cached) {
            // For Cartesian boxes, a union is componentwise min/max.
            // Cache only bounds at the legal right-group starts.
            std::array<Box, 129> suffix;
            auto right = rtree::elements_box<Box>(elements_copy.begin() + index_last - 1,
                                                  elements_copy.end(), translator, strategy);
            for (size_t j = index_last - 1; ; --j) {
                suffix[j] = right;
                if (j == index_first) break;
                index::detail::expand(right, rtree::element_indexable(elements_copy[j - 1], translator), strategy);
            }
            auto left = rtree::elements_box<Box>(elements_copy.begin(),
                                                 elements_copy.begin() + index_first, translator, strategy);
            for (size_t i = index_first; i < index_last; ++i) {
                score_cut(i, left, suffix[i]);
                if (i + 1 < index_last)
                    index::detail::expand(left, rtree::element_indexable(elements_copy[i], translator), strategy);
            }
        } else {
            for (size_t i = index_first; i < index_last; ++i) {
                Box left = rtree::elements_box<Box>(elements_copy.begin(), elements_copy.begin() + i, translator, strategy);
                Box right = rtree::elements_box<Box>(elements_copy.begin() + i, elements_copy.end(), translator, strategy);
                score_cut(i, left, right);
            }
        }

"""
    path = local / split_relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(split[:first] + replacement + split[last:], encoding="utf-8")
    _spatial_overlap_header(local)
    gcc("-std=c++17", "-I", NATIVE, "-I", local, "-I", LIBRARIES / "include",
        NATIVE / "rtree.cpp", "-o", BIN / "rtree.exe", cxx=True)


def paramd():
    source = unpack_sources("paramd.zip")
    # The upstream matrix loader is POSIX-only. The unchanged ordering
    # kernel takes the same caller-owned CSR interface on Windows.
    inputs = [source / "src" / name for name in ("config.cpp", "paramd.cpp", "utils.cpp")]
    gcc("-shared", "-std=c++17", "-fopenmp", "-I", source / "include", NATIVE / "paramd.cpp",
        *inputs, "-o", BIN / "paramd.dll", cxx=True)


def rust():
    for name in RUST:
        part = name.split("-1.", 1)[0]
        with tarfile.open(asset(name)) as archive:
            for member in archive:
                prefix = Path(member.name).parts
                component = f"rust-std-{RUST_TARGET}" if part == "rust-std" else part
                if not member.isfile() or len(prefix) < 4 or prefix[1] != component:
                    continue
                relative = Path(*prefix[2:])
                if relative.parts[0] not in ("bin", "lib", "libexec") or ".." in relative.parts:
                    continue
                path = RUST_HOME / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("wb") as output, archive.extractfile(member) as input_:
                    shutil.copyfileobj(input_, output)
    run([RUST_HOME / "bin" / "rustc.exe", "--version"])
    run([RUST_HOME / "bin" / "cargo.exe", "--version"])


def _build_bsdiff6(*, order_fix):
    source = unpack_sources("bsdiff6.zip")
    target_dir = source / "target"
    if order_fix:
        fixed = SOURCES / "bsdiff6-orderfix"
        shutil.copytree(source, fixed, dirs_exist_ok=True, ignore=shutil.ignore_patterns("target"))
        source = fixed
        matcher = source / "crates" / "bsdiff" / "src" / "block_alignment" / "match_block.rs"
        code = matcher.read_text(encoding="utf-8")
        code = replace_once(code, "#[derive(Debug, PartialEq)]", "#[derive(Debug)]")
        code = replace_once(code, "        self.partial_cmp(other).unwrap()", """        self.0.total_cmp(&other.0)
            .then_with(|| self.1.cmp(&other.1))
            .then_with(|| self.2.total_cmp(&other.2))""")
        code = replace_once(code, "impl Eq for HeapItem {}", """impl PartialEq for HeapItem {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}

impl Eq for HeapItem {}""")
        matcher.write_text(code, encoding="utf-8")
    rust_bin = RUST_HOME / "bin"
    env = dict(os.environ)
    env.update({
        "PATH": os.pathsep.join((str(rust_bin), str(HOST_TOOLCHAIN / "bin"), env.get("PATH", ""))),
        "CARGO_HOME": str(WORK / "cargo"),
        "CARGO_TARGET_DIR": str(target_dir),
        "CARGO_BUILD_JOBS": "4",
        "CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER": str(HOST_TOOLCHAIN / "bin" / "gcc.exe"),
    })
    run([rust_bin / "cargo.exe", "build", "--locked", "--release", "--target", RUST_TARGET], cwd=source, env=env)
    target = target_dir / RUST_TARGET / "release"
    candidates = list(target.glob("*.exe"))
    if len(candidates) != 1:
        raise RuntimeError(f"expected one bsdiff6 executable in {target}, found {candidates}")
    output = "bsdiff6-orderfix.exe" if order_fix else "bsdiff6.exe"
    shutil.copy2(candidates[0], BIN / output)


def bsdiff6():
    _build_bsdiff6(order_fix=False)


def bsdiff6_orderfix():
    _build_bsdiff6(order_fix=True)


def archive_codec():
    source = NATIVE / "archive-codec"
    rust_bin = RUST_HOME / "bin"
    target = WORK / "archive-codec"
    env = dict(os.environ)
    env.update({
        "PATH": os.pathsep.join((str(rust_bin), str(HOST_TOOLCHAIN / "bin"), env.get("PATH", ""))),
        "CARGO_HOME": str(WORK / "cargo"),
        "CARGO_TARGET_DIR": str(target),
        "CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER": str(HOST_TOOLCHAIN / "bin" / "gcc.exe"),
    })
    run([rust_bin / "cargo.exe", "build", "--locked", "--release", "--target", RUST_TARGET], cwd=source, env=env)
    shutil.copy2(target / RUST_TARGET / "release" / "wheel_deflate.dll", BIN / "wheel_deflate.dll")


def main():
    builders = {"cuba": cuba, "delta": delta, "rust": rust, "bsdiff6": bsdiff6, "packages": packages,
                "bsdiff6-orderfix": bsdiff6_orderfix,
                "sparse": sparse, "spatial": spatial, "paramd": paramd, "archive-codec": archive_codec}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", choices=builders)
    args = parser.parse_args()
    BIN.mkdir(parents=True, exist_ok=True)
    SOURCES.mkdir(parents=True, exist_ok=True)
    for target in args.targets:
        builders[target]()


if __name__ == "__main__":
    main()
