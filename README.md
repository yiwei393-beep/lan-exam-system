# 局域网考试系统

## 试卷一键导入

教师登录后进入：

```text
教师端 -> 试卷管理 -> 导入试卷
```

支持格式：

```text
JSON
Markdown
Excel .xlsx
```

推荐使用 JSON，字段最完整，也最不容易出错。

支持题型：

```text
single_choice    单选题
multiple_choice  多选题
true_false       判断题
short_answer     简答题
```

## JSON 模板

```json
{
  "paper_id": "P20260605001",
  "title": "Web前端基础测试",
  "duration_minutes": 90,
  "questions": [
    {
      "question_no": 1,
      "type": "single_choice",
      "content": "HTML 的全称是什么？",
      "options": [
        { "key": "A", "value": "HyperText Markup Language" },
        { "key": "B", "value": "HyperText Machine Language" },
        { "key": "C", "value": "HighText Markup Language" },
        { "key": "D", "value": "HyperTool Markup Language" }
      ],
      "answer": "A",
      "score": 5
    },
    {
      "question_no": 2,
      "type": "multiple_choice",
      "content": "下面哪些属于前端基础技术？",
      "options": [
        { "key": "A", "value": "HTML" },
        { "key": "B", "value": "CSS" },
        { "key": "C", "value": "JavaScript" },
        { "key": "D", "value": "SQLite" }
      ],
      "answer": "A,B,C",
      "score": 10
    },
    {
      "question_no": 3,
      "type": "true_false",
      "content": "CSS 可以控制网页样式。",
      "answer": "true",
      "score": 5
    },
    {
      "question_no": 4,
      "type": "short_answer",
      "content": "请简述前端、后端、数据库分别负责什么。",
      "keywords": ["前端", "后端", "数据库"],
      "score": 15
    }
  ]
}
```

## Markdown 模板

```markdown
# Web前端基础测试
时长：90分钟

1. [单选题] HTML 的全称是什么？
A. HyperText Markup Language
B. HyperText Machine Language
C. HighText Markup Language
D. HyperTool Markup Language
答案：A
分值：5

2. [判断题] CSS 可以控制网页样式。
答案：正确
分值：5

3. [简答题] 请简述前端、后端、数据库分别负责什么。
关键词：前端,后端,数据库
分值：15
```

## Excel 表头

第一行表头可以使用中文：

```text
题号 | 题型 | 题目 | A | B | C | D | 答案 | 分值 | 关键词
```

也可以使用英文：

```text
question_no | type | content | A | B | C | D | answer | score | keywords
```

题型可填写：

```text
单选题 / single_choice
多选题 / multiple_choice
判断题 / true_false
简答题 / short_answer
```

## 部署更新

这次新增功能涉及：

```text
app.py
static/app.js
README.md
```

上传 GitHub 覆盖后，Render 会自动重新部署。
