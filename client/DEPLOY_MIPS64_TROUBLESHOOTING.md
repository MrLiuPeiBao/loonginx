# Loongnix MIPS64 开发板部署问题记录

## 目录

- [1. 文档目的](#1-文档目的)
- [2. 部署环境与目标](#2-部署环境与目标)
- [3. 问题总览](#3-问题总览)
- [4. 详细问题与解决过程](#4-详细问题与解决过程)
  - [4.1 部署脚本因 CRLF 换行失败](#41-部署脚本因-crlf-换行失败)
  - [4.2 虚拟环境 activate 与 `set -u` 不兼容](#42-虚拟环境-activate-与-set--u-不兼容)
  - [4.3 系统 Python 版本过低](#43-系统-python-版本过低)
  - [4.4 `yum` 仓库不可用](#44-yum-仓库不可用)
  - [4.5 编译 Python 3.9 缺少 `ffi.h`](#45-编译-python-39-缺少-ffih)
  - [4.6 `make altinstall` 阶段缺少 `zlib`](#46-make-altinstall-阶段缺少-zlib)
  - [4.7 面向该开发板编译 Python 3.9 的完整流程](#47-面向该开发板编译-python-39-的完整流程)
- [5. 最终结果](#5-最终结果)
- [6. 当前可直接复用的操作命令](#6-当前可直接复用的操作命令)
- [7. 关闭 ttyS0 串口登录、开机自动连接 WiFi、SSH 免密登录与固定 IP](#7-关闭-ttys0-串口登录开机自动连接-wifissh-免密登录与固定-ip)
- [8. 后续建议](#8-后续建议)

## 1. 文档目的

本文档用于记录 `client` 项目部署到 Loongnix MIPS64 开发板过程中遇到的实际问题、出现原因、解决方法、命令和结果，便于后续重复部署、团队交接和问题复盘。

整理原则如下：

- 内容完整，但避免重复表述
- 每个问题都说明“现象、原因、解决方案原理、命令、结果”
- 对存在多种解决方法的问题，给出方案对比
- 最终保留一套可直接复用的部署与运行命令

## 2. 部署环境与目标

### 2.1 部署目标

将项目目录 `client` 部署到开发板，并在开发板上完成：

- Python 运行环境准备
- 虚拟环境创建
- 依赖安装
- 项目代码同步
- 程序运行验证

### 2.2 开发板环境

部署过程中确认到的环境信息如下：

```bash
uname -a
Linux MiWiFi-R3600-srv 3.10.0-693.fc21.loongson.2k.12.mips64el+ #115 SMP Mon Nov 8 18:00:11 PST 2021 mips64 mips64 mips64 GNU/Linux

python -V
Python 2.7.8

python3 -V
Python 3.4.1
```

包管理器情况：

```bash
apt
-bash: apt: 未找到命令

yum
已加载插件：langpacks
您需要给出命令
```

由此可以确认：

- 系统为 Loongnix / Fedora 21 系
- 架构为 `mips64el`
- 可用包管理器为 `yum`
- 系统自带 `python3` 仅为 `3.4.1`

### 2.3 项目路径

本地项目路径：

```text
C:\Users\lpb20\Desktop\loonginx\client
```

开发板部署路径：

```bash
/opt/loonginx-client
```

## 3. 问题总览

| 编号 | 问题 | 直接现象 | 根本原因 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | 脚本无法启动 | `pipefail` 报错、`$'\r'` 报错 | 脚本为 Windows `CRLF` 行尾 | 已解决 |
| 2 | 虚拟环境激活失败 | `_OLD_VIRTUAL_PATH: 未绑定的变量` | `activate` 与 `set -u` 组合不兼容 | 已解决 |
| 3 | Python 版本过低 | 脚本回退到 `python3.4.1` | 系统默认 Python 3 不满足项目要求 | 已解决 |
| 4 | `yum` 源不可用 | `Could not resolve host: ftp.loongnix.org` | 旧 repo 地址失效 | 已解决 |
| 5 | Python 编译缺少 libffi | `_ctypes.c: fatal error: ffi.h` | 未安装 `libffi-devel` | 已解决 |
| 6 | Python 安装阶段缺少 zlib | `zlib not available`，`ensurepip` 失败 | 未安装 `zlib-devel` | 已解决 |
| 7 | 如何得到适用于该板卡的 Python | 需要兼容 `mips64el`、Loongnix 1.0、老系统库 | 必须结合系统依赖和源码编译流程处理 | 已整理流程 |

## 4. 详细问题与解决过程

### 4.1 部署脚本因 CRLF 换行失败

#### 现象

首次执行部署脚本时出现错误：

```bash
bash client/scripts/deploy_mips64_board.sh /opt/loonginx-client
: 无效的选项名/deploy_mips64_board.sh: 第 2 行:set: pipefail
```

继续排查后又出现：

```bash
/root/client/scripts/deploy_mips64_board.sh:行6: $'\r': 未找到命令
```

#### 原因

脚本文件在 Windows 环境下保存为 `CRLF` 行尾，上传到 Linux 后，shell 会把 `\r` 当作实际字符处理，导致：

- `set -o pipefail\r` 被识别成非法参数
- 空行中的 `\r` 被识别成独立命令

#### 解决方法原理

将脚本从 Windows 行尾 `CRLF` 转换为 Unix 行尾 `LF`，Linux shell 才能正确解析脚本。

#### 解决方法对比

| 方案 | 原理 | 优点 | 缺点 |
| --- | --- | --- | --- |
| `sed -i 's/\r$//' file` | 直接删除每行结尾 `\r` | 快，原地修复 | 仅适合文本脚本 |
| `tr -d '\r' < old > new` | 删除所有回车字符后重写文件 | 兼容性高 | 需要临时文件 |
| Windows 端改为 LF | 从源头解决 | 最彻底 | 需要重新同步文件 |

#### 实际采用命令

```bash
sed -i 's/\r$//' /root/client/scripts/deploy_mips64_board.sh
chmod +x /root/client/scripts/deploy_mips64_board.sh
```

验证行尾是否正确：

```bash
sed -n '1,12p' /root/client/scripts/deploy_mips64_board.sh | cat -A
```

#### 结果

脚本可以继续执行，换行问题排除。

### 4.2 虚拟环境 activate 与 `set -u` 不兼容

#### 现象

脚本执行到虚拟环境阶段时报错：

```bash
/opt/loonginx-client/.venv/bin/activate:行6: _OLD_VIRTUAL_PATH: 未绑定的变量
```

#### 原因

旧系统或旧版虚拟环境生成的 `activate` 脚本，会先访问某些变量再判断是否赋值；而 `set -u` 要求一旦引用未定义变量就立即报错退出，因此二者组合会触发异常。

#### 解决方法原理

不要依赖 `source .venv/bin/activate`，而是直接调用虚拟环境中的解释器和 pip，可绕过 `activate` 脚本兼容性问题。

#### 解决方法对比

| 方案 | 原理 | 优点 | 缺点 |
| --- | --- | --- | --- |
| 去掉 `set -u` | 不再因未定义变量报错 | 改动小 | 失去部分严格检查 |
| 直接调用 `.venv/bin/python` 和 `.venv/bin/pip` | 绕过 `activate` | 最稳定，适合自动化 | 需要改脚本 |
| 局部临时关闭 `set -u` | 仅在激活阶段放宽约束 | 保留部分严格性 | 逻辑较复杂 |

#### 实际采用方法

部署脚本改为：

- 不再依赖 `source activate`
- 直接调用虚拟环境中的 `python` 和 `pip`
- 同时避免全局 `set -u` 带来的兼容性问题

#### 结果

虚拟环境创建和后续依赖安装可继续执行。

### 4.3 系统 Python 版本过低

#### 现象

脚本执行时显示：

```bash
[deploy][warn] python3.9 not found, trying python3
[deploy] using python3: Python 3.4.1
```

#### 原因

开发板系统自带的 `python3` 是 `3.4.1`。该版本对现代项目和第三方库过旧，可能带来以下问题：

- `venv` 行为和现代版本差异较大
- 新版依赖不再支持 Python 3.4
- 安装工具链兼容性差

#### 解决方法原理

为项目准备独立的 Python 3.9+ 运行环境，避免依赖系统过旧的 Python 3.4.1。

#### 解决方法对比

| 方案 | 原理 | 优点 | 缺点 |
| --- | --- | --- | --- |
| 使用系统现成高版本 Python | 指定已有解释器 | 最省事 | 前提是系统已安装 |
| `yum install python39` | 通过仓库安装 | 维护成本低 | 老系统不一定提供 |
| 源码编译安装到 `/opt/python3.9` | 自行构建独立运行时 | 可控性最高 | 需要编译依赖和时间 |
| 拷贝预编译 Python 目录 | 复用其他机器产物 | 快 | 要求环境高度一致 |

#### 实际采用方法

在部署脚本中加入以下策略：

1. 优先寻找现成 `Python 3.9+`
2. 找不到则尝试 `yum` 安装
3. 若仓库无法直接提供，则编译 `Python 3.9.19`
4. 安装位置固定为 `/opt/python3.9`

编译核心命令为：

```bash
cd /usr/local/src/Python-3.9.19
make distclean
./configure --prefix=/opt/python3.9 --with-ensurepip=install
make -j2
make altinstall
```

#### 结果

最终成功安装：

```bash
/opt/python3.9/bin/python3.9 -V
Python 3.9.19
```

### 4.4 `yum` 仓库不可用

#### 现象

执行 `yum` 安装时出现：

```bash
Could not resolve host: ftp.loongnix.org
```

#### 原因

最初的仓库配置使用了不可用或过期的域名 `ftp.loongnix.org`，而开发板实际能够访问的是 `ftp.loongnix.cn`。

#### 解决方法原理

将 repo 配置改为可用的 Loongnix 官方仓库地址，并重新建立 `yum` 缓存。

#### 解决方法对比

| 方案 | 原理 | 优点 | 缺点 |
| --- | --- | --- | --- |
| 修改 repo 到 `ftp.loongnix.cn` | 使用正确仓库地址 | 标准做法 | 依赖网络可用 |
| 修改 `/etc/hosts` | 用静态域名映射绕过 DNS | 修复快 | 不适合长期维护 |
| 修改 DNS | 提高域名解析成功率 | 对 DNS 故障有效 | 如果 repo 地址本身错则无效 |
| 离线安装 RPM | 不依赖在线仓库 | 稳定 | 准备成本高 |

#### 实际采用命令

先备份原 repo：

```bash
mkdir -p /etc/yum.repos.d.bak-20260316
mv /etc/yum.repos.d/*.repo /etc/yum.repos.d.bak-20260316/ 2>/dev/null
```

写入新的 repo：

```bash
cat >/etc/yum.repos.d/fedora.repo <<'EOF'
[fedora]
name=Fedora $releasever - $basearch
failovermethod=priority
baseurl=http://ftp.loongnix.cn/os/loongnix/1.0/os/
enabled=1
metadata_expire=7d
gpgcheck=0
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-$releasever-$basearch
skip_if_unavailable=False

[fedora-debuginfo]
name=Fedora $releasever - $basearch - Debug
failovermethod=priority
baseurl=http://ftp.loongnix.cn/os/loongnix/1.0/debug/
enabled=0
metadata_expire=7d
gpgcheck=0
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-$releasever-$basearch
skip_if_unavailable=False

[fedora-source]
name=Fedora $releasever - Source
failovermethod=priority
baseurl=http://ftp.loongnix.cn/os/loongnix/1.0/SRPMS/
enabled=0
metadata_expire=7d
gpgcheck=0
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-$releasever-$basearch
skip_if_unavailable=False
EOF
```

验证命令：

```bash
yum clean all
rm -rf /var/cache/yum/*
yum makecache
yum repolist
```

#### 结果

仓库恢复正常：

```bash
yum makecache
元数据缓存已建立

yum repolist
fedora   Fedora 21 - mips64el   40,245
```

### 4.5 编译 Python 3.9 缺少 `ffi.h`

#### 现象

编译 Python 3.9 时出现：

```bash
/usr/local/src/Python-3.9.19/Modules/_ctypes/_ctypes.c:107:17: 致命错误：ffi.h：没有那个文件或目录
```

#### 原因

`_ctypes` 模块依赖 `libffi`。系统缺少开发头文件 `ffi.h`，说明未安装 `libffi-devel`。

#### 解决方法原理

安装 `libffi-devel`，为 Python 编译过程提供头文件与链接信息。

#### 解决方法对比

| 方案 | 原理 | 优点 | 缺点 |
| --- | --- | --- | --- |
| 安装 `libffi-devel` | 提供 `ffi.h` | 最标准、最稳妥 | 依赖仓库可用 |
| 自定义 `CPPFLAGS/LDFLAGS` | 指定 libffi 自定义路径 | 灵活 | 前提是文件已存在 |
| 跳过 `_ctypes` | 不编译该模块 | 可临时绕过 | Python 功能不完整，不推荐 |

#### 实际采用命令

```bash
yum install -y libffi-devel
```

为避免后续继续缺依赖，实际一次性安装了完整构建依赖：

```bash
yum install -y zlib-devel libffi-devel openssl-devel sqlite-devel readline-devel bzip2-devel xz-devel
```

#### 结果

`ffi.h` 缺失问题排除，Python 3.9 可继续编译。

### 4.6 `make altinstall` 阶段缺少 `zlib`

#### 现象

安装阶段出现：

```bash
ModuleNotFoundError: No module named 'zlib'
zipimport.ZipImportError: can't decompress data; zlib not available
...
make: *** [altinstall] Error 1
```

#### 原因

Python 在 `ensurepip` 阶段需要解压内置 wheel 包；若没有编译出 `zlib` 模块，就无法完成解压。开发板当时只有 `zlib` 运行库，没有 `zlib-devel` 头文件。

#### 解决方法原理

安装 `zlib-devel` 后，重新 `configure` 和编译，让 Python 重新探测并构建 `zlib` 模块。

#### 解决方法对比

| 方案 | 原理 | 优点 | 缺点 |
| --- | --- | --- | --- |
| 安装 `zlib-devel` 并重新编译 | 提供 `zlib.h` | 最标准 | 需要重新编译 |
| 自行编译 zlib 并设置路径 | 提供自定义依赖 | 可用于极端环境 | 复杂度高 |
| `--without-ensurepip` | 跳过 ensurepip | 可暂时绕过安装阶段 | pip 不可用，不推荐 |

#### 实际采用命令

```bash
yum install -y zlib-devel
```

然后重新清理并编译：

```bash
cd /usr/local/src/Python-3.9.19
make distclean
./configure --prefix=/opt/python3.9 --with-ensurepip=install
make -j2
make altinstall
```

#### 结果

Python 3.9 成功安装，验证如下：

```bash
/opt/python3.9/bin/python3.9 -V
Python 3.9.19

/opt/python3.9/bin/python3.9 -c "import zlib, ssl, sqlite3, ctypes, venv; print('ok')"
ok

/opt/python3.9/bin/python3.9 -m pip --version
pip 23.0.1 from /opt/python3.9/lib/python3.9/site-packages/pip (python 3.9)
```

### 4.7 面向该开发板编译 Python 3.9 的完整流程

这一节单独总结“为什么这块板子必须这样编 Python，以及怎样编出来的 Python 才真正可用”。

#### 4.7.1 为什么不能直接用系统 Python

开发板自带的是：

```bash
python3 -V
Python 3.4.1
```

该版本过旧，主要问题有：

- 与当前项目和现代依赖的兼容性差
- `venv`、`pip`、`setuptools` 行为较老
- 很多第三方库已不再支持 Python 3.4

因此，最稳妥的方式不是继续适配系统 Python，而是为板卡单独安装一套 Python 3.9。

#### 4.7.2 为什么选择源码编译

对于这块 Loongnix 1.0、`mips64el` 开发板，源码编译是最合适的方案，原因是：

- 系统较老，仓库里不一定直接提供可用的 `python39`
- 板卡架构不是常见的 `x86_64`，也不是 `loongarch64`
- 直接拿其他机器预编译的 Python 复制过来，容易遇到 ABI、动态库和系统依赖不匹配

源码编译的核心原理是：

1. 先补齐 Python 编译所需的系统开发包
2. 使用本机编译器在目标板上直接生成与当前系统 ABI 匹配的解释器
3. 将 Python 安装到独立目录，例如 `/opt/python3.9`
4. 使用这套 Python 创建虚拟环境和安装项目依赖

#### 4.7.3 编译前必须满足的条件

至少需要两类条件：

1. 基础编译工具

```bash
gcc --version
make --version
tar --version
```

2. Python 编译所需开发包

```bash
zlib-devel
libffi-devel
openssl-devel
sqlite-devel
readline-devel
bzip2-devel
xz-devel
```

如果缺少这些包，就会出现前文已经遇到过的典型错误：

- 缺少 `libffi-devel` 会导致 `_ctypes` 编译失败
- 缺少 `zlib-devel` 会导致 `ensurepip` 失败

#### 4.7.4 安装编译依赖

在修复 `yum` 仓库后，使用以下命令安装依赖：

```bash
yum install -y zlib-devel libffi-devel openssl-devel sqlite-devel readline-devel bzip2-devel xz-devel
```

这一步的原理是为 Python 的可选扩展模块提供头文件和库文件。Python 源码中的很多标准库模块并不是纯 Python，而是依赖系统开发包编译出来的 C 扩展。

#### 4.7.5 获取 Python 源码

使用 Python 官方源码包：

```bash
cd /usr/local/src
wget https://www.python.org/ftp/python/3.9.19/Python-3.9.19.tgz
tar xf Python-3.9.19.tgz
cd Python-3.9.19
```

如果现场网络受限，也可以先在其他机器下载再拷贝到板上，原理相同。

#### 4.7.6 编译与安装命令

```bash
cd /usr/local/src/Python-3.9.19
make distclean
./configure --prefix=/opt/python3.9 --with-ensurepip=install
make -j2
make altinstall
```

各步骤的作用如下：

- `make distclean`
  清理之前失败编译留下的探测结果和中间文件，避免缺依赖时生成的错误状态继续污染本次编译。

- `./configure --prefix=/opt/python3.9 --with-ensurepip=install`
  生成当前系统对应的编译配置，并指定安装路径到 `/opt/python3.9`。

- `make -j2`
  开始并行编译。`-j2` 是相对保守的设置，适合资源较紧的开发板，避免过高并发造成内存压力。

- `make altinstall`
  安装 `python3.9`，但不覆盖系统默认的 `python` 或 `python3`，这是在老系统上最稳妥的做法。

#### 4.7.7 为什么使用 `altinstall` 而不是 `install`

两者区别如下：

| 命令 | 原理 | 优点 | 风险 |
| --- | --- | --- | --- |
| `make install` | 可能覆盖系统默认 `python3` 相关链接 | 使用方便 | 可能破坏系统已有工具链 |
| `make altinstall` | 仅安装 `python3.9` 等版本化命令 | 对老系统最安全 | 启动时需要显式写完整路径 |

对这块开发板，应优先使用：

```bash
make altinstall
```

因为系统本身依赖老版本 Python，覆盖系统解释器的风险较高。

#### 4.7.8 编译成功后的验证方法

仅看到 `make altinstall` 跑完还不够，必须做运行验证。建议按以下顺序检查：

1. 查看版本：

```bash
/opt/python3.9/bin/python3.9 -V
```

2. 验证关键标准库模块：

```bash
/opt/python3.9/bin/python3.9 -c "import zlib, ssl, sqlite3, ctypes, venv; print('ok')"
```

3. 验证 pip：

```bash
/opt/python3.9/bin/python3.9 -m pip --version
```

4. 验证虚拟环境：

```bash
/opt/python3.9/bin/python3.9 -m venv /tmp/py39test
/tmp/py39test/bin/python -V
rm -rf /tmp/py39test
```

这些检查分别覆盖：

- 解释器是否能启动
- 编译时关键扩展模块是否完整
- `ensurepip` 是否成功
- `venv` 是否可用于项目部署

#### 4.7.9 本次实际验证结果

本次编译后的验证结果为：

```bash
/opt/python3.9/bin/python3.9 -V
Python 3.9.19

/opt/python3.9/bin/python3.9 -c "import zlib, ssl, sqlite3, ctypes, venv; print('ok')"
ok

/opt/python3.9/bin/python3.9 -m pip --version
pip 23.0.1 from /opt/python3.9/lib/python3.9/site-packages/pip (python 3.9)
```

这说明编译出的 Python 已满足当前项目部署要求。

#### 4.7.10 备选方案对比

如果从更长远的维护角度看，还存在其他方案：

| 方案 | 原理 | 优点 | 缺点 | 适用场景 |
| --- | --- | --- | --- | --- |
| 板上源码编译 | 在目标板本机编译 | 与系统最匹配，最稳妥 | 时间较长 | 单板部署、首次落地 |
| 预编译后整体复制 `/opt/python3.9` | 在同构板卡上编好再复制 | 批量部署快 | 必须同系统、同架构、同依赖 | 同批量板卡 |
| 自建 RPM | 把 Python 打成包统一安装 | 最便于规模化管理 | 前期成本高 | 批量交付 |
| 继续使用系统 Python 3.4 | 不新增解释器 | 最省事 | 风险最高，兼容性最差 | 不推荐 |

## 5. 最终结果

当前已确认的结果如下：

- 部署脚本可正常执行
- `yum` 源已修复并可正常使用
- Python 3.9.19 已成功安装到 `/opt/python3.9`
- `pip`、`zlib`、`ssl`、`sqlite3`、`ctypes`、`venv` 均验证通过
- `client` 已成功部署到 `/opt/loonginx-client`
- 主程序可以启动

关键验证输出如下：

```bash
/opt/python3.9/bin/python3.9 -V
Python 3.9.19

/opt/python3.9/bin/python3.9 -c "import zlib, ssl, sqlite3, ctypes, venv; print('ok')"
ok

/opt/python3.9/bin/python3.9 -m pip --version
pip 23.0.1 from /opt/python3.9/lib/python3.9/site-packages/pip (python 3.9)
```

部署脚本执行成功：

```bash
PYTHON_BIN=/opt/python3.9/bin/python3.9 bash /root/client/scripts/deploy_mips64_board.sh /opt/loonginx-client
...
[deploy] deployment finished
```

程序运行验证表明，Python 编译与项目部署链路已经打通。

## 6. 当前可直接复用的操作命令

### 6.1 重新部署项目

```bash
PYTHON_BIN=/opt/python3.9/bin/python3.9 \
bash /root/client/scripts/deploy_mips64_board.sh /opt/loonginx-client
```

### 6.2 前台启动程序

```bash
cd /opt/loonginx-client
/opt/loonginx-client/.venv/bin/python /opt/loonginx-client/main.py
```

### 6.3 停止前台程序

```bash
Ctrl+C
```

### 6.4 配置开机自启

```bash
cat >/etc/systemd/system/loonginx-client.service <<'EOF'
[Unit]
Description=Loonginx Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/loonginx-client
EnvironmentFile=-/opt/loonginx-client/.env
ExecStart=/opt/loonginx-client/.venv/bin/python /opt/loonginx-client/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

```bash
systemctl daemon-reload
systemctl enable loonginx-client
systemctl start loonginx-client
systemctl status loonginx-client --no-pager
journalctl -u loonginx-client -n 100 --no-pager
```

### 6.5 查看和修改配置

查看配置文件：

```bash
sed -n '1,260p' /opt/loonginx-client/config.py
grep -nE 'MQTT|PLC|RFID|SERIAL|HOST|PORT|ENABLE|BROKER' /opt/loonginx-client/config.py
```

编辑配置文件：

```bash
vi /opt/loonginx-client/config.py
```

如果使用 `systemd` 且项目读取环境变量，也可以编辑：

```bash
vi /opt/loonginx-client/.env
systemctl restart loonginx-client
```

## 7. 关闭 ttyS0 串口登录、开机自动连接 WiFi、SSH 免密登录与固定 IP

这一部分记录的是本次开发板联调过程中新增暴露出来的运行环境问题，以及最终验证通过的修复方法。目标不是“串口自动登录 root”，而是让开发板在上电后具备以下行为：

- `ttyS0` 不再被登录服务抢占，可留给 RFID 或其他串口设备使用
- 无需先登录本地用户，开发板即可自动连接 WiFi
- SSH 可在板卡冷启动后直接恢复访问
- IP 地址尽量固定，便于后续远程维护

这部分不属于 Python 编译本身，但对开发板长期稳定运行非常关键，因此单独补充。

### 7.1 现场环境确认

在开发板上执行检查后，确认环境如下：

```bash
cat /proc/cmdline
console=ttyS0,115200 ...

systemctl status serial-getty@ttyS0.service --no-pager
Active: active (running)

lsof /dev/ttyS0
python ... /dev/ttyS0
login  ... /dev/ttyS0

grep -E '^RFID_SERIAL_PORT|^SERIAL_PORT' /opt/loonginx-client/.env
RFID_SERIAL_PORT=/dev/ttyS0
SERIAL_PORT=/dev/ttyS4

nmcli connection show "402_CF" | grep -E 'connection.permissions|connection.autoconnect'
connection.permissions: user:root
connection.autoconnect: yes

nmcli -f GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY dev show wlan0
GENERAL.CONNECTION: 402_CF
IP4.ADDRESS[1]:     192.168.31.187/24
```

同时还能在客户端日志中看到典型报错：

```text
RFID read error: device reports readiness to read but returned no data
(device disconnected or multiple access on port?)
```

由此可以定位出两个根因：

- `ttyS0` 同时被 `serial-getty@ttyS0.service` 和 `loonginx-client` 占用，导致 RFID 串口竞争
- WiFi 配置 `402_CF` 带有 `connection.permissions: user:root`，使得连接依赖 root 会话出现后才会被真正激活

这也解释了现场现象：

- 板卡上电后，未登录时 SSH 不可达
- 一旦本地登录 `root`，WiFi 才连上，SSH 才恢复
- RFID 日志持续刷串口读取错误

### 7.2 关闭 ttyS0 串口登录，释放给 RFID

#### 原因与原理

当前项目中 `RFID_SERIAL_PORT=/dev/ttyS0`，因此 `ttyS0` 已经属于业务串口，不适合继续承载本地登录控制台。若仍保留 `serial-getty@ttyS0`，就会和客户端进程同时打开同一设备，导致串口竞争。

本次最终采用的是“彻底关闭并屏蔽 `serial-getty@ttyS0`”，而不是“继续在 `ttyS0` 上做自动登录 root”。这样更符合当前板卡的实际用途：主维护方式是 SSH，`ttyS0` 则留给 RFID。

#### 解决方法对比

| 方案 | 原理 | 优点 | 缺点 |
| --- | --- | --- | --- |
| `systemctl mask --now serial-getty@ttyS0.service` | 彻底禁止 ttyS0 登录服务启动 | 能完全释放 `ttyS0`，最适合当前 RFID 场景 | 本地串口控制台不可再直接登录 |
| 配置 `agetty --autologin root` | 让 ttyS0 自动进入 root | 能免输密码 | 仍然会占用 `ttyS0`，不适合 RFID |
| 保持默认登录 | ttyS0 继续作为本地控制台 | 保留传统登录方式 | 与 RFID 冲突，且无助于无人值守启动 |

#### 实际采用命令

建议先做备份：

```bash
TS=$(date +%Y%m%d_%H%M%S)
BK=/root/codex-backups/$TS
mkdir -p "$BK"

cp -a /etc/systemd/system/serial-getty@ttyS0.service.d "$BK"/ttyS0-dropin 2>/dev/null || true
cp -a /etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf "$BK"/ 2>/dev/null || true
```

然后关闭并屏蔽该服务：

```bash
systemctl mask --now serial-getty@ttyS0.service
systemctl daemon-reload
```

#### 验证命令

```bash
systemctl is-enabled serial-getty@ttyS0.service
systemctl status serial-getty@ttyS0.service --no-pager
lsof /dev/ttyS0
```

#### 预期结果

- `serial-getty@ttyS0.service` 显示为 `masked`
- 服务状态为 `inactive (dead)`
- `lsof /dev/ttyS0` 中不再出现 `login` 或 `agetty`
- 只剩 `loonginx-client` 独占 `ttyS0`

#### 回退方法

如果后续确实需要恢复本地串口登录，可执行：

```bash
systemctl unmask serial-getty@ttyS0.service
systemctl daemon-reload
systemctl start serial-getty@ttyS0.service
```

### 7.3 开机自动连接指定 WiFi，且不依赖登录用户

#### 原因与原理

开发板已存在可用 WiFi 连接，并由 `NetworkManager` 管理；真正的问题不在于 `autoconnect` 没开，而在于连接配置被限制给特定用户：

```bash
nmcli connection show "402_CF" | grep connection.permissions
connection.permissions: user:root
```

当 `connection.permissions` 绑定到 `root` 之类的会话用户时，NetworkManager 在无人登录的冷启动阶段可能不会立即激活该连接。这样就会出现“必须先登录一次，本机 WiFi 才连上”的现象。

因此，修复关键点是：

- 清空目标 WiFi 连接的 `connection.permissions`
- 保持 `connection.autoconnect yes`
- 给目标 WiFi 足够高的自动连接优先级

#### 解决方法对比

| 方案 | 原理 | 优点 | 缺点 |
| --- | --- | --- | --- |
| 清空 `connection.permissions` | 将 WiFi 连接改为系统级自动连接 | 不依赖本地登录，适合无人值守启动 | 需确认该连接本身配置正确 |
| 保留 `user:root` 并在串口自动登录 root | 通过登录会话间接触发连接 | 表面上可用 | 会重新依赖 `ttyS0` 登录，不是根因修复 |
| 改用手工脚本调用 `ifup`/`wpa_supplicant` | 绕开 NM 的用户权限模型 | 可行 | 对当前系统更复杂，不推荐优先采用 |

#### 实际采用命令

```bash
nmcli con mod "402_CF" connection.interface-name wlan0
nmcli con mod "402_CF" 802-11-wireless.ssid "402_CF"
nmcli con mod "402_CF" connection.permissions ""
nmcli con mod "402_CF" connection.autoconnect yes
nmcli con mod "402_CF" connection.autoconnect-priority 100
nmcli con mod "402_CF" ipv6.method ignore

# 如果历史连接也带有用户权限限制，建议一并清空
nmcli con mod "Xiaomi_FBF7" connection.permissions ""

# 是否保留备用 WiFi 的自动连接，可按现场需要选择
# nmcli con mod "Xiaomi_FBF7" connection.autoconnect no

nmcli connection reload
nmcli con down "402_CF" || true
nmcli con up "402_CF"
```

#### 原理说明

- `connection.permissions ""`
  表示该连接不再依赖某个登录用户，而是允许系统在启动阶段直接激活

- `connection.autoconnect yes`
  表示开机后自动连接该 WiFi

- `connection.autoconnect-priority 100`
  表示在存在多个已保存 WiFi 时优先连接该配置

- `connection.interface-name wlan0`
  将连接绑定到当前无线网卡

- `ipv6.method ignore`
  在当前仅关注 IPv4 的前提下可减少无关因素

#### 验证命令

```bash
nmcli connection show "402_CF" | grep -E 'connection.permissions|connection.autoconnect|connection.autoconnect-priority'
nmcli con show --active
nmcli -f GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY dev show wlan0

journalctl -b -u NetworkManager --no-pager | \
  grep -E 'Auto-activating connection|Activation: successful'
```

#### 预期结果

- `connection.permissions` 为空
- 开机后 `wlan0` 自动连接 `402_CF`
- 不再需要先登录本地 `root` 才能联上 WiFi

### 7.4 推荐补充：配置 SSH 免密登录

#### 原因与原理

既然本次目标是让开发板在无人值守启动后即可远程维护，那么建议同时配置 SSH 公钥登录。这样可以避免继续依赖默认密码，也便于脚本化检查和自动化部署。

#### 建议做法

在 Windows 上位机生成专用密钥：

```powershell
ssh-keygen -t ed25519 -C "loonginx-board-auto-login" -f "$env:USERPROFILE\.ssh\loonginx_board_ed25519"
```

将公钥追加到开发板：

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
printf '%s\n' 'ssh-ed25519 <your-public-key> loonginx-board-auto-login' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Windows 本机可选配置 SSH 别名：

```text
Host loonginx-board
    HostName 192.168.31.187
    User root
    IdentityFile C:\Users\<your-user>\.ssh\loonginx_board_ed25519
    IdentitiesOnly yes
```

#### 验证命令

```powershell
ssh loonginx-board
```

或：

```powershell
ssh -i C:\Users\<your-user>\.ssh\loonginx_board_ed25519 root@192.168.31.187
```

### 7.5 固定 IP，不再每次变化

#### 原因

当前 `NetworkManager` 使用 DHCP 获取地址，从日志可以看到：

```bash
dhclient[4321]: bound to 192.168.31.187
```

DHCP 动态分配的特征是：

- 设备每次重连 WiFi 或租约变化时，IP 可能不同
- 不利于远程访问、脚本调用和服务绑定

因此需要将地址固定下来。

#### 解决方法对比

| 方案 | 原理 | 优点 | 缺点 | 推荐度 |
| --- | --- | --- | --- | --- |
| 路由器 DHCP 绑定 | 按设备 MAC 固定发放同一 IP | 最稳，不易冲突，改网段方便 | 需要能操作路由器 | 高 |
| 板卡本地静态 IP | 在板卡连接配置中写死地址 | 不依赖路由器后台 | 需手工避免与 DHCP 池冲突 | 中 |

#### 方案一：路由器 DHCP 绑定

这是优先推荐的方案。

先获取无线网卡 MAC：

```bash
cat /sys/class/net/wlan0/address
```

当前板卡无线网卡 MAC 为：

```text
f0:c8:14:7d:b0:c6
```

然后在路由器后台做 DHCP 保留，例如绑定到：

```text
192.168.31.50
```

板卡侧保持 DHCP：

```bash
nmcli con mod "402_CF" ipv4.method auto
nmcli con down "402_CF" || true
nmcli con up "402_CF"
```

该方案的原理是：

- 板卡仍按 DHCP 工作
- 路由器根据 MAC 永远分配同一个 IP
- 网络迁移时适应性更强

#### 方案二：板卡本地静态 IP

如果无法修改路由器配置，则可在板卡本地写死 IPv4 地址。

先查看默认网关：

```bash
ip route | grep default
```

若网关为 `192.168.31.1`，例如要固定为 `192.168.31.50/24`，可执行：

```bash
nmcli con mod "402_CF" ipv4.method manual
nmcli con mod "402_CF" ipv4.addresses 192.168.31.50/24
nmcli con mod "402_CF" ipv4.gateway 192.168.31.1
nmcli con mod "402_CF" ipv4.dns "223.5.5.5 119.29.29.29"
nmcli con mod "402_CF" connection.autoconnect yes
nmcli con mod "402_CF" connection.autoconnect-priority 100

nmcli con down "402_CF" || true
nmcli con up "402_CF"
```

#### 原理说明

- `ipv4.method manual`
  将连接切换为静态地址模式

- `ipv4.addresses`
  指定固定 IP 和掩码

- `ipv4.gateway`
  指定默认网关

- `ipv4.dns`
  指定 DNS 服务器，避免静态地址后丢失域名解析能力

#### 验证命令

```bash
ip addr show wlan0
ip route
nmcli con show "402_CF"
```

#### 回退到 DHCP 的命令

如果静态 IP 配置不正确，或后续需要恢复动态分配，可执行：

```bash
nmcli con mod "402_CF" ipv4.method auto
nmcli con down "402_CF" || true
nmcli con up "402_CF"
```

#### 结果

开发板可通过两种方式实现固定 IP，其中推荐优先使用“路由器 DHCP 绑定”，若现场不便修改路由器，则使用板卡本地静态 IP。

### 7.6 重启后的整体验证

完成上述配置后，建议统一重启验证：

```bash
reboot
```

重启后检查：

```bash
ssh root@192.168.31.187 "whoami && hostname"
nmcli con show --active
ip addr show wlan0
ip route
systemctl status sshd --no-pager
systemctl status serial-getty@ttyS0.service --no-pager
```

预期结果：

- 无需本地登录用户，SSH 可在开机后直接恢复访问
- 板卡自动连接 `402_CF`
- `serial-getty@ttyS0.service` 处于 `masked` / `inactive`
- `loonginx-client` 可以独占 `ttyS0`
- `wlan0` 的 IP 保持为预期固定地址，或由路由器始终分配同一个保留地址

本次实际冷启动验证结果为：

- 开发板执行 `reboot` 后，约 15 秒内可重新通过 SSH 访问
- `wlan0` 自动连接到 `402_CF`
- `sshd` 正常启动
- `serial-getty@ttyS0.service` 保持 `masked`

## 8. 后续建议

当前这份文档已经把重点收敛到“如何在该板卡上得到一套可用的 Python 3.9”。后续如果还要继续完善，优先建议做两件事：

1. 把 Python 3.9 的编译产物固化下来  
   例如保留 `/opt/python3.9` 的归档，或在同构板卡上复用，避免每次重新编译。

2. 把编译依赖和 Python 构建流程脚本化  
   这样后续换板、重刷系统或批量部署时，能够稳定复现本次结果。
