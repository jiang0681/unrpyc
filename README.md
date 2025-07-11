# Unrpyc - Ren'Py 脚本反编译器

> **本项目 fork 自：https://github.com/CensoredUsername/unrpyc**

**Unrpyc** 是一个用于反编译 Ren'Py 编译的 .rpyc 脚本文件的工具。

## 快速使用

### 基本用法
```bash
# 反编译单个文件
python unrpyc.py script.rpyc

# 反编译整个目录
python unrpyc.py game_folder/

# 覆盖已存在的文件
python unrpyc.py -c script.rpyc

# 查看帮助
python unrpyc.py --help
```

### 常用选项
- `-c, --clobber` - 覆盖现有输出文件
- `-t LANGUAGE, --translate LANGUAGE` - 使用指定语言翻译
- `--try-harder` - 尝试绕过常见混淆方法（较慢）
- `-d, --dump` - 显示 AST 结构而不是反编译

## 文件结构
```
unrpyc/
├── unrpyc.py           # 主程序
├── deobfuscate.py      # 反混淆工具
├── decompiler/         # 核心反编译模块
└── README.md           # 本文档
```

## 系统要求
- Python 3.9 或更高版本
- 支持 Ren'Py 8.x 到 6.18.0 版本

## 注意事项
- 默认不会覆盖现有文件，使用 `-c` 选项强制覆盖
- 对于 6.99.10 以下的 Ren'Py 版本，建议使用 `--no-init-offset` 选项
- 工具会自动检测并处理不同版本的 Ren'Py 文件格式

## 兼容性
此版本支持：
- **Ren'Py 8.x** - 完全支持
- **Ren'Py 6.18.0-7.x** - 支持（6.99.10以下需要 `--no-init-offset`）
- **Ren'Py 6.18.0以下** - 不支持（请使用 legacy 版本）

---
