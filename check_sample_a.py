import re
from html.parser import HTMLParser

src = open('design/sample-a-light.html', encoding='utf-8').read()

ok = True

def check(name, cond):
    global ok
    print(('PASS' if cond else 'FAIL'), name)
    if not cond:
        ok = False

# 1. div 嵌套平衡
class DivCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.maxd = 0
        self.bad = False
    def handle_starttag(self, tag, attrs):
        if tag == 'div':
            self.depth += 1
            self.maxd = max(self.maxd, self.depth)
    def handle_endtag(self, tag):
        if tag == 'div':
            self.depth -= 1
            if self.depth < 0:
                self.bad = True

p = DivCounter()
p.feed(src)
check('div 嵌套平衡（开闭相等）', p.depth == 0 and not p.bad)

# 2. 布局关键点
check('drag-bar 元素存在', 'class="drag-bar" id="dragBar"' in src)
check('order 布局：动画左(order1)/拖条(2)/对话右(3)', '.stage-pane { order:1; }' in src
      and '.drag-bar { order:2;' in src and '.chat-pane { order:3;' in src)
check('对话面板边线改到左侧', 'border-left:1px solid var(--line)' in src)
check('拖拽 JS 就位', 'col-resize' in src and "getElementById('dragBar')" in src)
check('工作台导航只留对话', '<a class="on">💬 对话</a>\n      </nav>' in src)

# 3. 个人中心
check('个人中心 section(id=center)', '<section id="center">' in src)
check('侧边栏：个人信息/我的作品/退出', '👤 个人信息' in src and '🗂 我的作品' in src and '↩ 退出登录' in src)
check('侧边栏选中态在"我的作品"', '<div class="side-item on">🗂 我的作品</div>' in src)
check('顶栏导航含"个人中心"', '<a class="on">👤 个人中心</a>' in src)
check('sidebar CSS 就位', '.side {' in src and '.side-item.on' in src and '.center-body' in src)

# 4. 无残留
check('无"我的作品"顶栏导航残留', '<a>🗂 我的作品</a>' not in src)
check('doc-bar 锚点更新（#center）', 'href="#center"' in src)

# 5. 文案原则未被破坏（复查）
check('无教师身份残留', '张老师' not in src and '分享给学生' not in src and '备课' not in src)
check('无开发者术语', 'btn-prev' not in src and '覆盖式' not in src)

print('RESULT:', 'ALL PASS' if ok else 'HAS FAILURES')
import sys
sys.exit(0 if ok else 1)