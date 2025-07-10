# Copyright (c) 2021-2024 CensoredUsername
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from .util import DecompilerBase, First, reconstruct_paraminfo, \
                  reconstruct_arginfo, split_logical_lines, Dispatcher

from . import atldecompiler

from renpy import ui, sl2
from renpy.ast import PyExpr
from renpy.text import text
from renpy.sl2 import sldisplayables as sld
from renpy.display import layout, behavior, im, motion, dragdrop, transform

# 主要API

def pprint(out_file, ast, options,
           indent_level=0, linenumber=1, skip_indent_until_write=False):
    return SL2Decompiler(out_file, options).dump(
        ast, indent_level, linenumber, skip_indent_until_write)

# 实现

class SL2Decompiler(DecompilerBase):
    """
    一个用于处理 renpy 屏幕语言 2 屏幕反编译到指定流的对象
    """

    def __init__(self, out_file, options):
        super(SL2Decompiler, self).__init__(out_file, options)

    # 这个字典是 类: 未绑定方法 的映射，用于确定对哪个 slast 类调用什么方法
    dispatch = Dispatcher()

    def print_node(self, ast):
        self.advance_to_line(ast.location[1])
        self.dispatch.get(type(ast), type(self).print_unknown)(self, ast)

    @dispatch(sl2.slast.SLScreen)
    def print_screen(self, ast):

        # 打印屏幕语句并创建块
        self.indent()
        self.write(f'screen {ast.name}')
        # 如果有参数，打印它们
        if ast.parameters:
            self.write(reconstruct_paraminfo(ast.parameters))

        # 打印内容
        first_line, other_lines = self.sort_keywords_and_children(ast)

        # 显然，屏幕内容是可选的
        self.print_keyword_or_child(first_line, first_line=True, has_block=bool(other_lines))
        if other_lines:
            with self.increase_indent():
                for line in other_lines:
                    self.print_keyword_or_child(line)

    @dispatch(sl2.slast.SLIf)
    def print_if(self, ast):
        # if 和 showif 共享大量相同的基础设施
        self._print_if(ast, "if")

    @dispatch(sl2.slast.SLShowIf)
    def print_showif(self, ast):
        # 所以对于 if 和 showif，我们只是调用一个带有额外参数的底层函数
        self._print_if(ast, "showif")

    def _print_if(self, ast, keyword):
        # 第一个条件命名为 if 或 showif，其余为 elif
        keyword = First(keyword, "elif")
        for condition, block in ast.entries:
            self.advance_to_line(block.location[1])
            self.indent()
            # 如果 condition 为 None，这是 else 子句
            if condition is None:
                self.write("else")
            else:
                self.write(f'{keyword()} {condition}')

            # 每个条件都有一个 slast.SLBlock 类型的块
            self.print_block(block, immediate_block=True)

    def print_block(self, ast, immediate_block=False):
        # 表示一个 SLBlock 节点，它是关键字参数和子元素的容器
        #
        # block 是 showif、if、use、用户定义的显示组件的子元素
        # 对于 showif、if 和 use，不允许在同一行上有关键字属性
        # 对于自定义显示组件，是允许的
        #
        # immediate_block: 布尔值，表示在 : 之前没有关键字属性，
        # 并且需要一个块
        first_line, other_lines = self.sort_keywords_and_children(
            ast, immediate_block=immediate_block)

        has_block = immediate_block or bool(other_lines)

        self.print_keyword_or_child(first_line, first_line=True, has_block=has_block)

        if other_lines:
            with self.increase_indent():
                for line in other_lines:
                    self.print_keyword_or_child(line)

            # 特殊情况，强制需要一个块，但没有内容
        elif immediate_block:
            with self.increase_indent():
                self.indent()
                self.write("pass")

    @dispatch(sl2.slast.SLFor)
    def print_for(self, ast):
        # 由于元组解包很困难，renpy 就放弃了，如果在 for 语句中尝试任何元组解包，
        # 就在 for 语句后插入 $ a,b,c = _sl2_i。检测这种情况并忽略这个 slast.SLPython 条目
        if ast.variable == "_sl2_i":
            variable = ast.children[0].code.source[:-9]
            children = ast.children[1:]
        else:
            variable = ast.variable.strip() + " "
            children = ast.children

        self.indent()
        if hasattr(ast, "index_expression") and ast.index_expression is not None:
            self.write(f'for {variable}index {ast.index_expression} in {ast.expression}:')

        else:
            self.write(f'for {variable}in {ast.expression}:')

        # for 不包含块，只是子节点列表
        self.print_nodes(children, 1)

    @dispatch(sl2.slast.SLContinue)
    def print_continue(self, ast):
        self.indent()
        self.write("continue")

    @dispatch(sl2.slast.SLBreak)
    def print_break(self, ast):
        self.indent()
        self.write("break")

    @dispatch(sl2.slast.SLPython)
    def print_python(self, ast):
        self.indent()

        # 从 slast.SLPython 对象中提取源代码。如果以换行符开头，
        # 将其打印为 python 块，否则将其打印为 $ 语句
        code = ast.code.source
        if code.startswith("\n"):
            code = code[1:]
            self.write("python:")
            with self.increase_indent():
                self.write_lines(split_logical_lines(code))
        else:
            self.write(f'$ {code}')

    @dispatch(sl2.slast.SLPass)
    def print_pass(self, ast):
        # 一个 pass 语句
        self.indent()
        self.write("pass")

    @dispatch(sl2.slast.SLUse)
    def print_use(self, ast):
        # use 语句需要重构它想要传递的参数
        self.indent()
        self.write("use ")
        args = reconstruct_arginfo(ast.args)
        if isinstance(ast.target, PyExpr):
            self.write(f'expression {ast.target}')
            if args:
                self.write(" pass ")
        else:
            self.write(f'{ast.target}')

        self.write(f'{args}')
        if hasattr(ast, 'id') and ast.id is not None:
            self.write(f' id {ast.id}')

        if hasattr(ast, "block") and ast.block:
            self.print_block(ast.block)

    @dispatch(sl2.slast.SLTransclude)
    def print_transclude(self, ast):
        self.indent()
        self.write("transclude")

    @dispatch(sl2.slast.SLDefault)
    def print_default(self, ast):
        # 一个默认语句
        self.indent()
        self.write(f'default {ast.variable} = {ast.expression}')

    @dispatch(sl2.slast.SLDisplayable)
    def print_displayable(self, ast, has_block=False):
        # slast.SLDisplayable 表示各种语句。我们可以通过分析调用的显示组件和样式属性
        # 来确定它表示什么语句。
        key = (ast.displayable, ast.style)
        nameAndChildren = self.displayable_names.get(key)

        if nameAndChildren is None and self.options.sl_custom_names:
            # 检查我们是否为这个显示组件注册了名称
            nameAndChildren = self.options.sl_custom_names.get(ast.displayable.__name__)
            self.print_debug(
                f'为显示组件 {ast.displayable} 替换了名称 "{nameAndChildren[0]}"')

        if nameAndChildren is None:
            # 这是一个我们不了解的（用户定义的）显示组件。
            # 后备方案：假设显示组件的名称与给定的样式属性匹配。
            # 这种情况经常发生。但是，由于这可能是错误的，我们必须打印调试消息
            nameAndChildren = (ast.style, 'many')
            self.print_debug(
    f'''警告：遇到了类型为 "{ast.displayable}" 的用户定义显示组件。
    不幸的是，用户定义显示组件的名称没有记录在编译文件中。
    现在将替换样式名称 "{ast.style}"。
    要检查这是否正确，请找到相应的 renpy.register_sl_displayable 调用。''')  # noqa

        (name, children) = nameAndChildren
        self.indent()
        self.write(name)
        if ast.positional:
            self.write(" " + " ".join(ast.positional))

        atl_transform = getattr(ast, 'atl_transform', None)
        # AST 不包含是否使用了 "has" 块的指示。
        # 我们将在任何可能的时候使用它（除了直接嵌套它们，或者如果它们不包含任何子元素），
        # 因为这会产生更清洁的代码。

        # 如果我们还没有在 has 块中，并且有一个单独的子元素是显示组件，
        # 它本身有子元素，并且这个子元素的行号在任何 atl 变换或关键字之后，
        # 我们可以安全地使用 has 语句
        if (not has_block
                and children == 1
                and len(ast.children) == 1
                and isinstance(ast.children[0], sl2.slast.SLDisplayable)
                and ast.children[0].children
                and (not ast.keyword
                     or ast.children[0].location[1] > ast.keyword[-1][1].linenumber)
                and (atl_transform is None
                     or ast.children[0].location[1] > atl_transform.loc[1])):

            first_line, other_lines = self.sort_keywords_and_children(ast, ignore_children=True)
            self.print_keyword_or_child(first_line, first_line=True, has_block=True)

            with self.increase_indent():
                for line in other_lines:
                    self.print_keyword_or_child(line)

                self.advance_to_line(ast.children[0].location[1])
                self.indent()
                self.write("has ")

                self.skip_indent_until_write = True
                self.print_displayable(ast.children[0], True)

        elif has_block:
            # has 块：现在假设没有任何类型的块存在
            first_line, other_lines = self.sort_keywords_and_children(ast)
            self.print_keyword_or_child(first_line, first_line=True, has_block=False)
            for line in other_lines:
                self.print_keyword_or_child(line)

        else:
            first_line, other_lines = self.sort_keywords_and_children(ast)
            self.print_keyword_or_child(first_line, first_line=True, has_block=bool(other_lines))

            with self.increase_indent():
                for line in other_lines:
                    self.print_keyword_or_child(line)

    displayable_names = {
        (behavior.AreaPicker, "default"):       ("areapicker", 1),
        (behavior.Button, "button"):            ("button", 1),
        (behavior.DismissBehavior, "default"):  ("dismiss", 0),
        (behavior.Input, "input"):              ("input", 0),
        (behavior.MouseArea, 0):                ("mousearea", 0),
        (behavior.MouseArea, None):             ("mousearea", 0),
        (behavior.OnEvent, 0):                  ("on", 0),
        (behavior.OnEvent, None):               ("on", 0),
        (behavior.Timer, "default"):            ("timer", 0),
        (dragdrop.Drag, "drag"):                ("drag", 1),
        (dragdrop.Drag, None):                  ("drag", 1),
        (dragdrop.DragGroup, None):             ("draggroup", 'many'),
        (im.image, "default"):                  ("image", 0),
        (layout.Grid, "grid"):                  ("grid", 'many'),
        (layout.MultiBox, "fixed"):             ("fixed", 'many'),
        (layout.MultiBox, "hbox"):              ("hbox", 'many'),
        (layout.MultiBox, "vbox"):              ("vbox", 'many'),
        (layout.NearRect, "default"):           ("nearrect", 1),
        (layout.Null, "default"):               ("null", 0),
        (layout.Side, "side"):                  ("side", 'many'),
        (layout.Window, "frame"):               ("frame", 1),
        (layout.Window, "window"):              ("window", 1),
        (motion.Transform, "transform"):        ("transform", 1),
        (sld.sl2add, None):                     ("add", 0),
        (sld.sl2bar, None):                     ("bar", 0),
        (sld.sl2vbar, None):                    ("vbar", 0),
        (sld.sl2viewport, "viewport"):          ("viewport", 1),
        (sld.sl2vpgrid, "vpgrid"):              ("vpgrid", 'many'),
        (text.Text, "text"):                    ("text", 0),
        (transform.Transform, "transform"):     ("transform", 1),
        (ui._add, None):                        ("add", 0),
        (ui._hotbar, "hotbar"):                 ("hotbar", 0),
        (ui._hotspot, "hotspot"):               ("hotspot", 1),
        (ui._imagebutton, "image_button"):      ("imagebutton", 0),
        (ui._imagemap, "imagemap"):             ("imagemap", 'many'),
        (ui._key, None):                        ("key", 0),
        (ui._label, "label"):                   ("label", 0),
        (ui._textbutton, "button"):             ("textbutton", 0),
        (ui._textbutton, 0):                    ("textbutton", 0),
    }

    def sort_keywords_and_children(self, node, immediate_block=False, ignore_children=False):
        # 对具有关键字和子元素的 SL 语句的内容进行排序
        # 返回排序内容的列表。
        #
        # node 是 SLDisplayable、SLScreen 或 SLBlock
        #
        # 在这一点之前，语句的名称和任何位置参数已经被输出，
        # 但块本身尚未创建。
        #   immediate_block: 布尔值，如果为 True，第一行上没有任何内容
        #   ignore_children: 不检查子元素，用于实现 "has" 语句

        # 从节点获取我们需要的所有数据
        keywords = node.keyword
        children = [] if ignore_children else node.children

        # 第一个行号，我们可以在其中插入没有明确行号的内容
        block_lineno = node.location[1]
        start_lineno = (block_lineno + 1) if immediate_block else block_lineno

        # 这些是可选的
        keyword_tag = getattr(node, "tag", None)  # 仅由 SLScreen 使用
        keyword_as = getattr(node, "variable", None)  # 仅由 SLDisplayable 使用
        # 三者都可以有它，但无论如何都是可选属性
        atl_transform = getattr(node, "atl_transform", None)

        # 关键字
        # 7.7/8.2 之前：行末的关键字可以没有参数，解析器对此是可以接受的。
        keywords_by_line = [(value.linenumber if value else None,
                             "keyword" if value else "broken",
                             (name, value)) for name, value in keywords]

        # 子元素
        children_by_line = [(child.location[1], "child", child) for child in children]

        # 现在我们必须确定所有事物的顺序。多个关键字可以在同一行，但子元素不能。
        # 我们不想完全相信行号，即使它们完全错误，我们仍然应该输出一个不错的文件。
        # 另外，关键字和子元素应该从一开始就按顺序排列，所以我们不应该打乱那个顺序。

        # 将关键字和子元素合并到单个有序列表中
        # 行号、类型、内容的列表
        contents_in_order = []
        keywords_by_line.reverse()
        children_by_line.reverse()
        while keywords_by_line and children_by_line:
            # 损坏的关键字：总是在任何子元素之前输出，这样我们可以轻松地将它们与之前的关键字合并
            if keywords_by_line[-1][0] is None:
                contents_in_order.append(keywords_by_line.pop())

            elif keywords_by_line[-1][0] < children_by_line[-1][0]:
                contents_in_order.append(keywords_by_line.pop())

            else:
                contents_in_order.append(children_by_line.pop())

        while keywords_by_line:
            contents_in_order.append(keywords_by_line.pop())

        while children_by_line:
            contents_in_order.append(children_by_line.pop())

        # 如果存在，合并 at transform
        if atl_transform is not None:
            atl_lineno = atl_transform.loc[1]

            for i, (lineno, _, _) in enumerate(contents_in_order):
                if lineno is not None and atl_lineno < lineno:
                    index = i
                    break
            else:
                index = len(contents_in_order)

            contents_in_order.insert(index, (atl_lineno, "atl", atl_transform))

            # TODO: 双重检查任何 atl 是否在任何 "at" 关键字之后？

        # 一行可以是以下任何一种
        # 一个子元素
        # 一个损坏的关键字
        # 一个关键字列表，可能后跟一个 atl 变换

        # 关键字行的累加器
        current_keyword_line = None

        # (行号, 类型, 内容....) 的数据结构
        # 可能的类型
        # "child"
        # "keywords"
        # "keywords_atl"
        # "keywords_broken"
        contents_grouped = []

        for (lineno, ty, content) in contents_in_order:
            if current_keyword_line is None:
                if ty == "child":
                    contents_grouped.append((lineno, "child", content))
                elif ty == "keyword":
                    current_keyword_line = (lineno, "keywords", [content])
                elif ty == "broken":
                    contents_grouped.append((lineno, "keywords_broken", [], content))
                elif ty == "atl":
                    contents_grouped.append((lineno, "keywords_atl", [], content))

            else:
                if ty == "child":
                    contents_grouped.append(current_keyword_line)
                    current_keyword_line = None
                    contents_grouped.append((lineno, "child", content))

                elif ty == "keyword":
                    if current_keyword_line[0] == lineno:
                        current_keyword_line[2].append(content)

                    else:
                        contents_grouped.append(current_keyword_line)
                        current_keyword_line = (lineno, "keywords", [content])

                elif ty == "broken":
                    contents_grouped.append(
                        (current_keyword_line[0], "keywords_broken",
                         current_keyword_line[2], content))
                    current_keyword_line = None

                elif ty == "atl":
                    if current_keyword_line[0] == lineno:
                        contents_grouped.append(
                            (lineno, "keywords_atl", current_keyword_line[2], content))
                        current_keyword_line = None
                    else:
                        contents_grouped.append(current_keyword_line)
                        current_keyword_line = None
                        contents_grouped.append((lineno, "keywords_atl", [], content))

        if current_keyword_line is not None:
            contents_grouped.append(current_keyword_line)

        # 我们需要为任何没有行号的损坏关键字分配行号。最好的猜测是
        # 前一个行号 + 1，除非不存在，在这种情况下它是第一个可用行
        for i in range(len(contents_grouped)):
            lineno = contents_grouped[i][0]
            ty = contents_grouped[i][1]
            if ty == "keywords_broken" and lineno is None:
                contents = contents_grouped[i][3]

                if i != 0:
                    lineno = contents_grouped[i - 1][0] + 1
                else:
                    lineno = start_lineno

                contents_grouped[i] = (lineno, "keywords_broken", [], contents)

        # 这两个关键字没有行号信息
        # 另外，从 7.3 向上，由于某种原因，tag 不能与 `screen` 放在同一行。
        # 目前不可能在同一个显示组件中同时有 `as` 和 `tag` 关键字
        # `as` 仅用于显示组件，`tag` 用于屏幕。
        # 策略：
        # - 如果在任何行之前有几个空行，我们可以为它们创建一些新行
        # - 如果第一行是关键字行，我们可以将它们与其合并
        # - 对于 'as'，我们也可以将其放在第一行
        if keyword_tag:
            # 如果没有内容，我们可以（奇怪地）将其放在第一行，
            # 因为没有内容的屏幕不会开始一个块。但为了理智起见，
            # 我们将把它放在之后的第一行
            if not contents_grouped:
                contents_grouped.append((block_lineno + 1, "keywords", [("tag", keyword_tag)]))

            # 或者如果块的第一行是空的，把它放在那里
            elif contents_grouped[0][0] > block_lineno + 1:
                contents_grouped.insert(0, (block_lineno + 1, "keywords", [("tag", keyword_tag)]))

            else:
                # 尝试找到一个可以合并的关键字行
                for entry in contents_grouped:
                    if entry[1].startswith("keywords"):
                        entry[2].append(("tag", keyword_tag))
                        break

                # 只是强制把它放在那里。这可能会干扰行号，但是
                # 很难知道在子元素之间的哪里放置它是安全的
                else:
                    contents_grouped.insert(
                        0, (block_lineno + 1, "keywords", [("tag", keyword_tag)]))

        if keyword_as:
            # 如果没有内容，将其放在第一个可用行
            if not contents_grouped:
                contents_grouped.append((start_lineno, "keywords", [("as", keyword_as)]))

            # 或者如果块的第一行是空的，把它放在那里
            elif contents_grouped[0][0] > block_lineno + 1:
                contents_grouped.insert(0, (block_lineno + 1, "keywords", [("as", keyword_as)]))

            # 如果开始行可用，我们也可以将其放在开始行
            elif contents_grouped[0][0] > start_lineno:
                contents_grouped.insert(0, (start_lineno, "keywords", [("as", keyword_as)]))

            else:
                # 尝试找到一个可以合并的关键字行
                for entry in contents_grouped:
                    if entry[1].startswith("keywords"):
                        entry[2].append(("as", keyword_as))
                        break

                # 只是强制把它放在那里。这可能会干扰行号，但是
                # 很难知道在子元素之间的哪里放置它是安全的
                else:
                    contents_grouped.insert(0, (start_lineno, "keywords", [("as", keyword_as)]))



        # 如果第一行没有内容，插入一个空行，以便更容易处理
        if immediate_block or not contents_grouped or contents_grouped[0][0] != block_lineno:
            contents_grouped.insert(0, (block_lineno, "keywords", []))

        # 返回 first_line_content, later_contents
        return contents_grouped[0], contents_grouped[1:]

    def print_keyword_or_child(self, item, first_line=False, has_block=False):
        sep = First(" " if first_line else "", " ")

        lineno = item[0]
        ty = item[1]

        if ty == "child":
            self.print_node(item[2])
            return

        if not first_line:
            self.advance_to_line(lineno)
            self.indent()

        for name, value in item[2]:
            self.write(sep())
            self.write(f'{name} {value}')

        if ty == "keywords_atl":
            assert not has_block, "不能在与 at transform 块同一行开始块"
            self.write(sep())
            self.write("at transform:")

            self.linenumber = atldecompiler.pprint(
                self.out_file, item[3], self.options,
                self.indent_level, self.linenumber, self.skip_indent_until_write
            )
            self.skip_indent_until_write = False
            return

        if ty == "keywords_broken":
            self.write(sep())
            self.write(item[3])

        if first_line and has_block:
            self.write(":")
