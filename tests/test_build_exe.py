"""
build_exe 打包参数测试
========================
锁住四条容易被改坏的约束：

1. --add-data 的源路径和目标路径必须一致。hqchart_bridge.py 用
   Path(__file__).parent 定位 assets/，即 <包目录>/assets；目标一旦写成
   assets，文件会被平铺到解包根目录，K线窗口会静默回退成 matplotlib 版。
2. --collect-all webview 必须在。漏了它 WebView2 的 DLL 不会被打进去。
3. LICENSE / NOTICE 必须被打进产物。Apache 2.0 第 4(a)(d) 条要求分发时
   随附许可证和归属声明，而 onefile 不会自动带上仓库根目录的文件。
4. 第三方依赖的许可证也要收进产物。它们的文件都在 dist-info 里，
   PyInstaller 不会自动带上。
"""

import os
from pathlib import Path

import build_exe


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _add_data_pairs(args):
    """取出所有 --add-data 后面的 源;目标 对"""
    pairs = []
    for idx, token in enumerate(args):
        if token != "--add-data":
            continue
        raw = args[idx + 1]
        src, sep, dst = raw.partition(os.pathsep)
        assert sep, "--add-data 需要 源%s目标 两段" % os.pathsep
        pairs.append((src, dst))
    return pairs


def _assets_pair(args):
    """找出指向 assets 目录的那一条 --add-data"""
    for src, dst in _add_data_pairs(args):
        if os.path.basename(src) == "assets":
            return src, dst
    raise AssertionError("--add-data 里没有 assets 条目")


def test_add_data_source_and_dest_are_identical():
    src, dst = _assets_pair(build_exe.build_args())

    assert os.path.normpath(src) == os.path.normpath(dst)


def test_add_data_target_keeps_package_prefix():
    """目标路径必须保留 stock_monitor/ 前缀，否则会平铺到解包根目录"""
    src, dst = _assets_pair(build_exe.build_args())

    assert dst == os.path.join("stock_monitor", "assets")


def test_add_data_source_exists():
    src, _ = _assets_pair(build_exe.build_args())

    assert os.path.isdir(src), "%s 在仓库里不存在" % src


def test_license_files_are_packaged_at_bundle_root():
    """LICENSE / NOTICE 必须被打进产物，且落在解包根目录"""
    pairs = _add_data_pairs(build_exe.build_args())
    at_root = {src for src, dst in pairs if dst in (".", "")}

    for name in build_exe.LICENSE_FILES:
        assert name in at_root, "%s 没有被打进产物" % name


def test_license_files_exist_in_repo():
    for name in build_exe.LICENSE_FILES:
        path = os.path.join(PROJECT_ROOT, name)
        assert os.path.isfile(path), "仓库根目录缺少 %s" % name


def test_license_is_apache_2_0_with_hqchart_copyright():
    """防止 LICENSE 被换成别的东西，或忘记填 HQChart 的版权行"""
    text = open(os.path.join(PROJECT_ROOT, "LICENSE"),
                encoding="utf-8").read()

    assert "Apache License" in text
    assert "Version 2.0" in text
    assert "Copyright 2018 jones" in text
    # 官方模板的占位符必须被替换掉
    assert "[yyyy]" not in text
    assert "name of copyright owner" not in text


def test_notice_names_the_upstream_repo():
    """归属声明里要有上游仓库地址和用到的文件，否则第 4(d) 条不完整"""
    text = open(os.path.join(PROJECT_ROOT, "NOTICE"),
                encoding="utf-8").read()

    assert "https://github.com/jones2000/HQChart" in text
    for name in ("umychart.js", "umychart.complier.js",
                 "umychart.index.data.js"):
        assert name in text


def test_notice_mentions_license_file():
    text = open(os.path.join(PROJECT_ROOT, "NOTICE"),
                encoding="utf-8").read()

    assert "LICENSE" in text


def _staged_files(tmp_path):
    """列出暂存目录里实际拷进来的文件，相对 third_party/"""
    dest = tmp_path / "third_party"
    found = []
    for dirpath, _dirnames, filenames in os.walk(dest):
        for name in filenames:
            full = Path(dirpath) / name
            found.append(os.path.relpath(str(full), str(dest)))
    return sorted(found)


def test_stage_licenses_copies_real_files(tmp_path):
    dest = build_exe.stage_third_party_licenses(str(tmp_path))

    assert dest is not None, "没有收集到任何第三方许可证文件"
    assert os.path.isdir(dest)
    files = _staged_files(tmp_path)
    assert files, "暂存目录是空的"
    for rel in files:
        assert os.path.isfile(os.path.join(dest, rel))


def test_stage_licenses_are_unique(tmp_path):
    """不同包的 LICENSE 不能被互相覆盖：目录名必须带发行名"""
    build_exe.stage_third_party_licenses(str(tmp_path))

    files = _staged_files(tmp_path)
    assert len(files) == len(set(files))
    for rel in files:
        parts = rel.split(os.sep)
        assert len(parts) == 2, "%s 不在 <发行名>/LICENSE 的布局里" % rel
        assert parts[0] != parts[1], "%s 的目录名和文件名一样，可能覆盖了" % rel


def test_stage_licenses_no_collision(tmp_path):
    """至少两个不同包，且各自的许可证都在自己的目录里"""
    build_exe.stage_third_party_licenses(str(tmp_path))

    labels = {rel.split(os.sep)[0] for rel in _staged_files(tmp_path)}
    assert len(labels) >= 2, "只收集到一个包：%s" % labels


def test_stage_licenses_returns_none_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setattr(build_exe, "_iter_license_files", lambda package: [])

    assert build_exe.stage_third_party_licenses(str(tmp_path)) is None


def test_build_args_includes_third_party_when_staged(tmp_path, monkeypatch):
    """传了暂存目录就要出现 third_party 条目，且源路径必须是相对路径"""
    monkeypatch.chdir(PROJECT_ROOT)  # PyInstaller 按 cwd 解析相对源路径
    build_exe.stage_third_party_licenses(str(tmp_path))

    pairs = _add_data_pairs(build_exe.build_args(stage_root=str(tmp_path)))
    third_party = [src for src, dst in pairs if dst == "third_party"]

    assert third_party, "暂存目录没有被打进产物"
    # 用相对路径，避免归档条目名带上本机绝对路径
    for src in third_party:
        assert not os.path.isabs(src)


def test_build_args_without_stage_has_no_third_party():
    """不传暂存目录时不应有任何 third_party 条目"""
    pairs = _add_data_pairs(build_exe.build_args())

    assert not [1 for _, dst in pairs if dst == "third_party"]


def test_onefile_is_selected():
    assert "--onefile" in build_exe.build_args()


def test_entry_point_is_the_dispatcher():
    """入口必须是 main.py，因为只有它处理 --hqchart-chart 分发"""
    args = build_exe.build_args()

    assert args[-1] == os.path.join("stock_monitor", "main.py")
    assert os.path.isfile(args[-1])


def test_pywebview_is_collected():
    args = build_exe.build_args()

    assert "--collect-all" in args
    assert "webview" in args


def test_build_args_is_repeatable():
    """重复调用不应累积参数"""
    first = build_exe.build_args()
    second = build_exe.build_args()

    assert first == second
