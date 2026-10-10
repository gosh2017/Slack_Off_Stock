"""
Windows 打包脚本（PyInstaller onefile）
=========================================
用法：
    python build_exe.py

产物：
    dist/stock_monitor.exe        单文件，直接拷贝到其他机器即可运行

为什么要单独写这个脚本，不能自己敲 PyInstaller 命令：

  1. K线窗口是独立子进程（hqchart_chart.py）。开发环境用 `python -m` 拉起它，
     打包后 sys.executable 就是 exe 本身，-m 不再有效，所以要靠
     --hqchart-chart 参数区分入口（见 stock_monitor/main.py）。

  2. assets/ 必须显式打进去，而且 --add-data 的目标路径要是
     stock_monitor/assets 而不是 assets。写错的话文件会被平铺到解包根目录，
     而 hqchart_bridge.py 用 Path(__file__).parent 定位的是 <包目录>/assets，
     于是 K线窗口会静默回退成 matplotlib 版——程序不报错，但交互没了。

  3. 许可证文件也必须打进去。Apache 2.0 第 4(a) 条要求把许可证副本交给
     接收方，第 4(d) 条要求保留 NOTICE 里的归属声明，而 onefile 不会自动带上
     仓库根目录的文件。第三方依赖的许可证文件都在各自的 dist-info 里，
     同样不会被打包，所以这里把用到的包的 LICENSE / NOTICE 一起收集进来。

关于体积：Python 运行时、matplotlib、pywebview 的 WebView2 依赖和
HQChart 的 JS 全打进去，实测约 130MB。

已知限制：
    1. onefile 每次启动会先把文件解到临时目录。拉起 K线窗口相当于再起一个
       自身进程，也就是重新解一次包（约 1 秒）。
    2. 打包后的「查询历史」「监控列表」会写进解包的临时目录，退出即丢失
       （history_manager.py / watchlist_manager.py 用 __file__ 定位 config/）。
       想让它们生效，需要把这两个文件的 config 路径改成
       sys.executable 所在目录。
"""

import os
import shutil
import subprocess
import sys

NAME = "stock_monitor"
ENTRY = os.path.join("stock_monitor", "main.py")
# 源路径和目标路径必须一致：见上面的说明第 2 条
ASSETS_REL = os.path.join("stock_monitor", "assets")

# 随产物一起分发的许可证/声明文件（都在仓库根目录）
LICENSE_FILES = ("LICENSE", "NOTICE")

# 需要带上许可证文本的第三方包。填发行名，不是 import 名
THIRD_PARTY_LICENSE_PACKAGES = (
    "pywebview",
    "matplotlib",
    "requests",
    "Pillow",
)
# 第三方许可证的暂存目录（构建产物，已加入 .gitignore）
STAGE_DIR = ".stage"
PYWEBVIEW_HOOK_DIR = None



def _find_pywebview_hook_dir():
    """pywebview 自带 PyInstaller 钩子目录，找不到就跳过"""
    try:
        import webview
    except ImportError:
        return None
    candidate = os.path.join(os.path.dirname(webview.__file__), "__pyinstaller")
    return candidate if os.path.isfile(
        os.path.join(candidate, "hook-webview.py")) else None


def _iter_license_files(package):
    """列出某个已安装包自带的许可证文件，返回 [(发行名, 绝对路径), ...]

    发行名带版本号（如 requests-2.34.2），用它做子目录名，避免不同包的
    LICENSE 重名互相覆盖。
    """
    try:
        import importlib.metadata as md
        dist = md.distribution(package)
    except Exception:
        return []
    label = "%s-%s" % (dist.metadata["Name"], dist.version)
    keys = ("license", "notice", "copying")
    return [(label, dist.locate_file(f)) for f in (dist.files or [])
            if "dist-info" in str(f)
            and any(k in str(f).lower() for k in keys)]


def stage_third_party_licenses(stage_root):
    """把第三方包的许可证文件拷进暂存目录，返回打进去的目录路径。

    第三方包的许可证在各自的 dist-info 里、路径各不相同，直接一个个传给
    PyInstaller 很啰嗦，所以先按「发行名/LICENSE」的布局拷成一个目录，
    再整体打包。发行名带版本号，避免不同包的 LICENSE 重名互相覆盖。
    一个都没拷到就返回 None。
    """
    dest = os.path.join(stage_root, "third_party")
    count = 0
    for package in THIRD_PARTY_LICENSE_PACKAGES:
        for label, src in _iter_license_files(package):
            if not os.path.isfile(src):
                continue
            target = os.path.join(dest, label, os.path.basename(src))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(src, target)
            count += 1
    return dest if count else None


def build_args(stage_root=None):
    """组装 PyInstaller 命令行（不含解释器和模块名，便于测试）

    参数：
        stage_root: 第三方许可证的暂存目录。默认 None = 不收集第三方许可证。
    """
    global PYWEBVIEW_HOOK_DIR
    PYWEBVIEW_HOOK_DIR = _find_pywebview_hook_dir()

    args = [
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", NAME,
        "--paths", ".",
        "--add-data", "%s%s%s" % (ASSETS_REL, os.pathsep, ASSETS_REL),
    ]
    for license_file in LICENSE_FILES:
        # 目标写成 "." = 解包根目录，与仓库根目录布局一致
        args += ["--add-data", "%s%s." % (license_file, os.pathsep)]
    if stage_root:
        license_dir = stage_third_party_licenses(stage_root)
        if license_dir:
            # PyInstaller 按当前目录解析 --add-data 的相对源路径，
            # 所以这里必须用相对 cwd 的路径
            args += ["--add-data",
                     "%s%s%s" % (os.path.relpath(license_dir, os.getcwd()),
                                 os.pathsep, "third_party")]
    args += ["--collect-all", "webview"]
    if PYWEBVIEW_HOOK_DIR:
        args += ["--additional-hooks-dir", PYWEBVIEW_HOOK_DIR]
    return args + [ENTRY]


def _remove_tree(path):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
    except OSError:
        pass


def main():
    stage = os.path.join(os.path.dirname(os.path.abspath(__file__)), STAGE_DIR)
    try:
        _remove_tree(stage)
        os.makedirs(stage)
        args = build_args(stage_root=stage)
        print("== PyInstaller ==")
        for a in args:
            print("   ", a)
        print()
        return subprocess.call([sys.executable, "-m", "PyInstaller"] + args)
    except OSError as e:
        print("准备暂存目录失败：%s" % e, file=sys.stderr)
        return 1
    finally:
        _remove_tree(stage)


if __name__ == "__main__":
    sys.exit(main())
