"""PyInstaller 入口：打包成 exe 后双击时走这里。"""

from covenant.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
