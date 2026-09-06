# ASI 产品上传助手 macOS 打包说明

这个目录是 macOS 专用交付包。原 Windows 版本不需要改；macOS 必须在 Mac 电脑上打包，不能在 Windows 上直接生成可用的 `.app`。

## 0. 与 Windows 版本的功能同步

本目录已与 Windows 主版本代码同步，包含以下新功能：

- 「产品选项」页：统一控制 Distributor Only View、New Product、SEO、Close Out、Product Confirmed、Prop 65、Unimprinted 等勾选项。
- 「自动更新选填字段」按钮：登录 ASI 后只读抓取动态字段和候选值（`asi_field_sync.py`）。
- 材质别名（Material Custom Name）支持，找不到材质时回退到 Other。
- 价格档位支持 8 档（Q1–Q8 / P1–P8）。
- Windows 上密码使用 DPAPI 加密保存；macOS 无 DPAPI，密码为明文保存，行为不变。

macOS 平台适配（硬件码读取 `IOPlatformUUID`、配置保存在 `~/Library/Application Support`、浏览器路径检测、`open` 命令打开文件）全部保留，`tests/test_mac_platform_support.py` 专门验证这些行为。

## 1. 准备环境

建议使用 Python 3.11 或 3.12。

```bash
cd mac_version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install PySide6 pandas openpyxl DrissionPage loguru "cryptography>=42,<44" "pyinstaller>=6,<7"
```

如果 Mac 上已经安装 `uv`，也可以尝试：

```bash
uv sync --group build
```

## 2. 运行源码测试

```bash
python asi_ui.py
```

电脑需要安装 Google Chrome 或 Microsoft Edge。程序会自动检测这些常见路径：

```text
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge
```

如果自动检测不到，可以在界面里手动选择浏览器的可执行文件。

## 3. 运行单元测试

```bash
python -m unittest discover -s tests -v
```

其中 `test_mac_platform_support.py` 专门验证 macOS 适配：激活硬件码、配置目录、浏览器路径、打开文件。

## 4. 打包 macOS App

```bash
pyinstaller asi_upload_mac.spec --clean --noconfirm
```

打包完成后，结果在：

```text
dist/ASI产品上传助手.app
```

如果需要分发给其他 Mac 用户，可能还需要由打包者做 Apple 签名、公证，或让用户首次打开时右键选择“打开”。

## 4.1 使用 GitHub Actions 云端打包

项目根目录已经包含 GitHub Actions 工作流：

```text
.github/workflows/build-macos.yml
```

上传到 GitHub 后，可以在网页上手动打包：

1. 打开 GitHub 仓库页面。
2. 点击顶部的 `Actions`。
3. 左侧选择 `Build macOS App`。
4. 点击右侧 `Run workflow`。
5. 等待任务完成。
6. 打开完成的任务，在页面底部 `Artifacts` 下载 `ASI-product-upload-macOS`。

下载后得到的是：

```text
ASI-product-upload-macOS.zip
```

解压后就是 macOS 的 `.app`。

## 5. 激活流程

激活程序不需要改。

1. Mac 用户打开 `ASI产品上传助手.app`。
2. 软件显示硬件码。
3. Mac 用户把硬件码发给你。
4. 你用现有激活程序生成 `DDLIC3...` 激活码。
5. Mac 用户把激活码粘贴回软件完成激活。

macOS 版本只改变硬件码的来源：优先读取 Mac 的 `IOPlatformUUID`。激活码格式、签名校验、产品 ID、许可证文件加密逻辑都保持不变。

## 6. 许可证和配置保存位置

macOS 版本会把配置和许可证保存到用户目录下：

```text
~/Library/Application Support/ASIProductUpload/Client/settings.json
~/Library/Application Support/ASI_PRODUCT_UPLOAD/Licenses/asi_product_upload/license.bin
```

这和 Windows 版本的 `%LOCALAPPDATA%` 保存位置不同，但不影响激活程序生成激活码。
