# 习题课题库系统

面向高中数学备课的本地题库：收题、分类检索、组题、审校、生成学生版和教师版讲义。后端使用 Python 标准库与 SQLite，前端使用原生 JavaScript。

## 快速体验

需要 Python 3.10 或更高版本。

```sh
python run.py --demo
```

打开终端显示的本机地址（默认 `http://127.0.0.1:8760`）。第一次启动会生成 **12 道虚构演示题、2 个来源、2 份题单**，覆盖单选、多选、填空和解答。Windows 也可双击 `启动-演示数据.bat`。

演示题A展示审校完成的流程，演示题B展示待审流程；这些状态仅供功能演示，不代表真实教师审校。

开始自己的空题库：

```sh
python run.py
```

默认地址为 `http://127.0.0.1:8761`，数据库为 `data/qbank.db`。只监听本机，无需注册账号。

## 主要功能

- 收题箱、原始出处与题目版本记录。
- 知识点/方法分组检索，试卷类别、年级、年份筛选及常用方案。
- 题单分节、留白、题型与难度统计。
- 讲义排版工作台：A4快速预览、实际PDF校样、逐题留白/分页/选项排列，学生/答案/教师三版共用排版设置。
- 题目纠错、审校、版本比较与题目关系。
- AI识题、独立解题、自复核、标签与难度建议，支持手动重设题面区域和预算限制。
- 多模型接入：OpenAI兼容、Responses、Claude、Gemini四种协议，支持自定义地址、模型和本地服务。
- LaTeX讲义导出、使用反馈与私有完整备份。

## 可选依赖

普通题库功能无需安装额外 Python 包。PDF/图片处理需要：

```sh
python -m pip install -r requirements.txt
```

在AI工作台配置自己的服务地址、模型与密钥，或设置该接口的密钥环境变量。支持DeepSeek、OpenAI、Claude、Gemini、通义千问、硅基流动预设，以及Ollama、LM Studio和自定义兼容服务。项目不含密钥，不会在演示启动时发送AI请求。只有用户启动处理或点击生成能力验证后才发送相应内容；调用费用由使用者承担。详见[多模型接口接入](docs/多模型接口接入.md)。

生成讲义还需自行安装包含中文支持的 TeX Live 或 MiKTeX，并保证 `xelatex` 和 `xdvipdfmx` 来自同一发行版。程序会为编译子进程建立可写临时目录。

## 文档与开发

- [操作说明](docs/操作说明.md) · [检索说明](docs/检索工作台使用说明.md) · [纠错功能](docs/题目纠错使用说明.md)
- [排版与预览](docs/讲义排版预览.md) · [多模型接口](docs/多模型接口接入.md)
- [AI工作台](docs/AI工作台使用说明.md) · [字段字典](docs/字段字典.md) · [API](docs/API.md)
- [开发与测试](docs/开发与测试.md) · [数据与隐私](docs/数据与隐私.md)

```sh
python scripts/test_backend.py
node scripts/test_frontend.mjs
python scripts/package_public.py --check
python scripts/package_public.py --output dist/question-bank-source.zip
```

开源打包器按文件白名单收集源代码，检查密钥、私人路径和联系方式，并排除数据库、原卷、截图、AI记录、备份与构建产物。普通功能“完整备份”用于迁移自己的题库，不能当作开源发布包。

## 许可证

本项目和虚构演示数据采用 [MIT License](LICENSE)。ExBook 模板的原作者信息和 MIT 许可保留在 `templates/exbook/`；其他依赖说明见 [NOTICE](NOTICE)。原始试卷、个人题库、封面插画及推广水印均不在源码包中。
