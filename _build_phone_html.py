from pathlib import Path

root = Path(__file__).resolve().parent
src = root.parent / "msfs-addon/PackageSources/html_ui/InGamePanels/FlyConomyPanel/FlyConomyPanel.html"
html = src.read_text(encoding="utf-8")
start = html.find('<div id="stageWrap">')
end = html.rfind("</ingame-ui>")
body = html[start:end]
out = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>FlyConomy</title>
  <style>
    html, body { margin:0; background:#0e1014; overflow:auto; }
  </style>
  <link rel="stylesheet" href="/phone/FlyConomyPanel.css" />
  <script>
    window.SimVar = { SetSimVarValue: function () { return Promise.resolve(); } };
    window.Coherent = { call: function () {}, trigger: function () {}, on: function () {} };
  </script>
  <script src="/phone/FlyConomyPanel.js"></script>
</head>
<body class="border-box">
""" + body + """
</body>
</html>
"""
dest = root / "ui/phone/index.html"
dest.write_text(out, encoding="utf-8")
print("wrote", dest, dest.stat().st_size)
