import ast
import re
import unittest
from pathlib import Path

class RefreshWorkspaceTests(unittest.TestCase):
    def test_refresh_serves_same_workspace_with_fresh_asset_urls(self):
        tree = ast.parse(Path('server.py').read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'refresh_workspace')
        function.decorator_list = []
        context = {'BASE': Path('.'), 're': re, 'Response': lambda **kwargs: kwargs}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'refresh_workspace', 'exec'), context)
        response = context['refresh_workspace']()
        html = response['content']
        self.assertEqual(response['headers']['Cache-Control'], 'no-store')
        self.assertIn('app.js?v=20261013-table-names', html)
        self.assertIn('modern_workspace.css?v=20261013-table-names', html)
        self.assertEqual(len(re.findall(r'<script src=', html)), len(re.findall(r'<script src=', Path('index.html').read_text())))
        self.assertNotIn('localStorage.clear', html)
        self.assertNotIn('indexedDB.deleteDatabase', html)

if __name__ == '__main__':
    unittest.main()
