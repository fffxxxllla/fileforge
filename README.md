# FileForge 2.0.1

FileForge 是一个 Windows 桌面应用，用于生成指定格式、指定精确字节数的空白文件。它不只是修改扩展名：PDF、Office、ZIP、图片、音频、视频容器和 SQLite 等都会写入对应格式的最小合法结构，因此可被常见软件识别和打开。

## 直接使用应用

双击 `FileForge.exe` 即可，不需要安装 Python，也不需要联网。

界面提供：

- 现代 Windows 风格、浅色/深色/跟随系统主题
- 47 类真实格式和约百个常见扩展名
- Windows 1024 进制与十进制 1000 进制单位
- 生成前显示精确字节数及资源管理器预计显示值
- 快捷容量、自动格式识别、安全覆盖和生成后格式验证
- 仅在生成过程中显示的动态进度状态
- 完成后直接打开文件或所在文件夹

## 容量单位说明

Windows 资源管理器使用 1024 进制计算容量，但历史上仍把结果显示成 KB、MB、GB。例如原版的 `10 MB = 10,000,000` 字节，在 Windows 中只会显示约 `9.54 MB`。

FileForge 2.0 默认按 Windows 的显示习惯计算：

| 界面单位 | 字节数 | 输入 10 后的实际大小 |
|---|---:|---:|
| KB · Windows 1024 | 1,024 | 10,240 字节 |
| MB · Windows 1024² | 1,048,576 | 10,485,760 字节 |
| GB · Windows 1024³ | 1,073,741,824 | 10,737,418,240 字节 |
| MB · 十进制 1000² | 1,000,000 | 10,000,000 字节 |

命令行中的 `KB`、`MB`、`GB`、`TB` 同样默认使用 1024 进制；`KiB`、`MiB`、`GiB`、`TiB` 是等价写法。需要十进制时使用 `KB1000`、`MB1000`、`GB1000` 或 `TB1000`。

容量数值支持任意小数，例如 `.1 MB`、`1.234 MB`、`2.71828 GB`。由于文件只能包含整数个字节，换算结果会四舍五入到最接近的 1 字节，界面会在生成前显示最终采用的精确字节数。

## 命令行用法

源码版仍保留命令行能力：

```powershell
python fileforge.py blank.pdf 10MB
python fileforge.py workbook.xlsx 2MiB
python fileforge.py archive.zip 100MB --overwrite
python fileforge.py unknown.xyz 1GB --format generic
python fileforge.py --list-formats
```

## 支持范围

- 文本与代码：TXT、Markdown、CSV、TSV、JSON、JSONL、XML、YAML、INI、HTML、CSS、JavaScript、TypeScript、Python、Java、C/C++、C#、Go、Rust、PHP、Ruby、Shell、PowerShell、SQL、TOML 等
- 文档与 Office：PDF、RTF、PostScript/EPS、DOCX、XLSX、PPTX、ODT、ODS、ODP、EPUB
- 图片：PNG、JPEG、GIF、WebP、BMP、TIFF、ICO、SVG、PPM、PGM、PBM
- 音频：WAV、AIFF、FLAC、MP3、MIDI
- 视频/媒体容器：MP4、M4V、M4A、MOV、AVI
- 压缩与归档：ZIP、CBZ、JAR、TAR、GZIP、BZIP2、XZ
- 数据库与交换：SQLite、DB、VCF、ICS
- 通用：BIN、DAT、RAW，或通过 `--format generic` 使用任意扩展名

不同格式存在不可突破的最小结构大小；经典 RIFF WAV/AVI/AIFF 的单文件长度字段上限约为 4 GiB。

## 精确大小与磁盘占用

工具保证资源管理器看到的逻辑文件大小与目标值逐字节相等。大型零填充区域会优先使用稀疏文件，因此本机“占用空间”可能小于“文件大小”；这不影响格式或逻辑大小。

ZIP、Office、EPUB 和 JAR 使用真实未压缩填充条目，结构与 CRC 均有效。XZ 使用拼接空数据流。XZ 标准长度必须是 4 的倍数；非 4 倍数目标的最后 1–3 字节属于兼容性尾随填充。

## 从源码运行和构建

```powershell
python -m pip install -r requirements.txt
python fileforge_app.py
```

构建单文件 Windows 应用：

```powershell
python -m pip install -r build-requirements.txt --target .build_deps
powershell -ExecutionPolicy Bypass -File .\build_app.ps1
```

生成结果位于 `dist\FileForge.exe`。该可执行文件没有商业代码签名，某些 Windows 环境第一次运行时可能显示信誉提示。

## 设计与实现参考

本版参考了以下高星开源项目的公开设计和工程思路，没有复制其业务代码：

- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)：现代 Tk 控件、主题和高 DPI 适配
- [Files](https://github.com/files-community/Files)：Windows Fluent 风格的信息层级与卡片布局
- [Microsoft PowerToys](https://github.com/microsoft/PowerToys)：工具型应用的设置结构和固定操作区
- [PyInstaller](https://github.com/pyinstaller/pyinstaller)：独立 Windows 可执行文件打包

第三方组件及其许可证见 `THIRD_PARTY_NOTICES.md`。
