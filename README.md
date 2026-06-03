# 局域网考试系统

这是一个可部署上线的轻量级考试系统，包含学生端、教师端、SQLite 数据库、Token 登录、自动保存、断点续考、交卷、监控、批改和成绩导出。

## 技术栈

- 后端：Python Flask
- 数据库：SQLite
- 前端：原生 HTML + CSS + JavaScript
- 部署：Render / Railway / 云服务器均可

## 默认账号

教师端：

- 账号：admin
- 密码：admin123

学生端：

- 登录名：学号
- 密码：学号后 5 位
- 已内置 `学生名单.xlsx` 中的 43 位学生账号

示例：

- 学号：2407040119
- 密码：40119
- 姓名确认：何濯启

## 本地运行

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

打开：

```text
http://127.0.0.1:5000
```

如果在同一个局域网机房使用，学生电脑访问教师机的局域网地址，例如：

```text
http://教师机IP:5000
```

## 部署上线

### Render

1. 把本项目上传到 GitHub。
2. 在 Render 创建 Web Service。
3. Build Command 填：

```bash
pip install -r requirements.txt
```

4. Start Command 填：

```bash
gunicorn app:app
```

5. 环境变量建议设置：

```text
SECRET_KEY=换成一串足够长的随机字符
```

部署完成后，Render 会提供一个公网网址，其他电脑可以直接访问。

### Railway

1. 新建 Railway 项目并连接 GitHub 仓库。
2. Railway 会自动识别 Python 项目。
3. 启动命令使用：

```bash
gunicorn app:app
```

4. 设置环境变量：

```text
SECRET_KEY=换成一串足够长的随机字符
```

## 主要功能

学生端：

- 学号和密码登录
- 姓名确认
- 考试须知
- 倒计时答题
- 题目导航
- 自动保存
- 断点续考
- 手动交卷
- 时间到自动提交
- 提交后禁止重复提交

教师端：

- 教师登录
- 系统仪表盘
- 学生列表
- 试卷列表
- 开始考试
- 结束考试
- 实时监控
- 强制收卷
- 简答题人工评分
- 成绩统计
- 成绩导出 CSV

## 数据库表

系统会自动创建以下数据表：

- students：学生表
- teachers：教师表
- papers：试卷表
- questions：题目表
- exams：考试表
- exam_records：考试记录表
- answers：答题记录表

## 小组分工示例

- 后端与数据库：数据库设计、API、登录认证、自动批改
- 教师端前端：仪表盘、考试管理、监控面板、成绩管理
- 学生端前端：登录、姓名确认、答题、倒计时、自动保存
- 测试与文档：联调测试、README、部署说明、演示材料

## 注意事项

- 第一次启动时会自动初始化数据库和 43 位学生账号。
- SQLite 数据库默认保存在 `instance/exam.sqlite`。
- 部署到公网后，建议修改教师默认密码和 `SECRET_KEY`。
- 如果要长期保存线上数据，不要删除部署平台的持久化磁盘或数据库文件。
