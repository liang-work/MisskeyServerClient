# Misskey Web Client

一个基于Python Flask后端和HTML/Vue.js前端的Misskey客户端。

## 功能特性

- 支持自定义Misskey服务器
- OAuth网页登录
- 时间线浏览 (Home/Local/Global)
- 发布帖子 (支持CW)
- 点赞/转推/收藏
- 通知查看
- 用户搜索

## 安装

1. 安装依赖:
```bash
pip install -r requirements.txt
```

2. 运行服务器:
```bash
python app.py
```

3. 打开浏览器访问 http://localhost:5000

## 使用方法

1. 输入你的Misskey服务器地址 (例如: https://utopia.pm)
2. 点击Login按钮
3. 在新打开的页面中完成授权
4. 授权完成后自动跳转到主页

## 技术栈

- 后端: Python Flask
- 数据库: SQLite (aiosqlite)
- 前端: HTML + Vue.js 3 + Bootstrap 5
