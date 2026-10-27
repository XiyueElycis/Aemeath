"""爱弥斯科技终端风格启动动画（静态 Web 资源）。

页面由 WPF 桌面端的 ``StartupWindow``（WebView2 全屏宿主）加载，
在 FastAPI 服务端通过 ``/startup`` 路径挂载（见 ``server.py``）。

目录::

    startup/
    └── web/
        ├── index.html       # 七阶段启动仪式结构
        ├── style.css        # 莱茵终端 × 爱弥斯视觉
        ├── boot.js          # 阶段编排 / 扫描 / 故障 / 音效 / 跳过
        └── assets/
            └── brand.png    # 与主界面侧边栏同一份品牌标识
"""
