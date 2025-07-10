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


from .util import DecompilerBase, First, WordConcatenator, reconstruct_paraminfo, \
                  reconstruct_arginfo, string_escape, split_logical_lines, Dispatcher, \
                  say_get_code, OptionBase
from .renpycompat import renpy

from operator import itemgetter
from io import StringIO

from . import sl2decompiler
from . import testcasedecompiler
from . import atldecompiler
from . import astdump

__all__ = ["astdump", "magic", "sl2decompiler", "testcasedecompiler", "translate", "util",
           "Options", "pprint", "Decompiler", "renpycompat"]

# 主要API

# 承载可配置反编译选项的对象
class Options(OptionBase):
    def __init__(self, indentation="    ", log=None,
                 translator=None, init_offset=False,
                 sl_custom_names=None):
        super(Options, self).__init__(indentation=indentation, log=log)

        # 反编译选项
        self.translator = translator
        self.init_offset = init_offset
        self.sl_custom_names = sl_custom_names

def pprint(out_file, ast, options=Options()):
    Decompiler(out_file, options).dump(ast)

# 实现

class Decompiler(DecompilerBase):
    """
    处理将renpy AST反编译到给定流的对象
    """

    # 这个字典是Class: unbound_method的映射，用于确定
    # 为哪个ast类调用什么方法
    dispatch = Dispatcher()

    def __init__(self, out_file, options):
        super(Decompiler, self).__init__(out_file, options)

        self.paired_with = False
        self.say_inside_menu = None
        self.label_inside_menu = None
        self.in_init = False
        self.missing_init = False
        self.init_offset = 0
        self.most_lines_behind = 0
        self.last_lines_behind = 0

    def advance_to_line(self, linenumber):
        self.last_lines_behind = max(
            self.linenumber + (0 if self.skip_indent_until_write else 1) - linenumber, 0)
        self.most_lines_behind = max(self.last_lines_behind, self.most_lines_behind)
        super(Decompiler, self).advance_to_line(linenumber)

    def save_state(self):
        return (super(Decompiler, self).save_state(), self.paired_with, self.say_inside_menu,
                self.label_inside_menu, self.in_init, self.missing_init, self.most_lines_behind,
                self.last_lines_behind)

    def commit_state(self, state):
        super(Decompiler, self).commit_state(state[0])

    def rollback_state(self, state):
        self.paired_with = state[1]
        self.say_inside_menu = state[2]
        self.label_inside_menu = state[3]
        self.in_init = state[4]
        self.missing_init = state[5]
        self.most_lines_behind = state[6]
        self.last_lines_behind = state[7]
        super(Decompiler, self).rollback_state(state[0])

    def dump(self, ast):
        if self.options.translator:
            self.options.translator.translate_dialogue(ast)

        if self.options.init_offset and isinstance(ast, (tuple, list)):
            self.set_best_init_offset(ast)

        # skip_indent_until_write避免初始空行
        super(Decompiler, self).dump(ast, skip_indent_until_write=True)
        # 如果有我们想要写出但还没有写的内容，现在就写
        for m in self.blank_line_queue:
            m(None)
        self.write("\n# 由unrpyc反编译: https://github.com/CensoredUsername/unrpyc\n")
        assert not self.missing_init, "缺少必需的init、init标签或translate块"

    def print_node(self, ast):
        # 我们在它们的打印方法中为某些类型特殊处理行前进，
        # 所以这里不要为它们前进行
        if hasattr(ast, 'linenumber') and not isinstance(
                ast, (renpy.ast.TranslateString, renpy.ast.With, renpy.ast.Label,
                      renpy.ast.Pass, renpy.ast.Return)
                ):
            self.advance_to_line(ast.linenumber)

        self.dispatch.get(type(ast), type(self).print_unknown)(self, ast)

    # ATL子反编译器钩子

    def print_atl(self, ast):
        self.linenumber = atldecompiler.pprint(
            self.out_file, ast, self.options,
            self.indent_level, self.linenumber, self.skip_indent_until_write
        )
        self.skip_indent_until_write = False

    # 可显示相关函数

    def print_imspec(self, imspec):
        if imspec[1] is not None:
            begin = f'expression {imspec[1]}'
        else:
            begin = " ".join(imspec[0])

        words = WordConcatenator(begin and begin[-1] != ' ', True)
        if imspec[2] is not None:
            words.append(f'as {imspec[2]}')

        if len(imspec[6]) > 0:
            words.append(f'behind {", ".join(imspec[6])}')

        if isinstance(imspec[4], str):
            words.append(f'onlayer {imspec[4]}')

        if imspec[5] is not None:
            words.append(f'zorder {imspec[5]}')

        if len(imspec[3]) > 0:
            words.append(f'at {", ".join(imspec[3])}')

        self.write(begin + words.join())
        return words.needs_space

    @dispatch(renpy.ast.Image)
    def print_image(self, ast):
        self.require_init()
        self.indent()
        self.write(f'image {" ".join(ast.imgname)}')
        if ast.code is not None:
            self.write(f' = {ast.code.source}')
        else:
            if ast.atl is not None:
                self.write(":")
                self.print_atl(ast.atl)

    @dispatch(renpy.ast.Transform)
    def print_transform(self, ast):
        self.require_init()
        self.indent()

        # 如果我们有一个具有非默认优先级的隐式init块，我们需要在这里
        # 存储优先级。
        priority = ""
        if isinstance(self.parent, renpy.ast.Init):
            init = self.parent
            if (init.priority != self.init_offset
                    and len(init.block) == 1
                    and not self.should_come_before(init, ast)):
                priority = f' {init.priority - self.init_offset}'
        self.write(f'transform{priority} {ast.varname}')
        if ast.parameters is not None:
            self.write(reconstruct_paraminfo(ast.parameters))

        # atl属性：自6.10以来
        if ast.atl is not None:
            self.write(":")
            self.print_atl(ast.atl)

    # 导演相关函数

    @dispatch(renpy.ast.Show)
    def print_show(self, ast):
        # 应用Ren'Py 8.4.0兼容性默认值
        if not hasattr(ast, 'atl'):
            ast.atl = None
        if not hasattr(ast, 'imspec'):
            ast.imspec = None
        if not hasattr(ast, 'layer'):
            ast.layer = None
        if not hasattr(ast, 'at_list'):
            ast.at_list = []
        if not hasattr(ast, 'onlayer'):
            ast.onlayer = None
        if not hasattr(ast, 'behind'):
            ast.behind = []
        if not hasattr(ast, 'zorder'):
            ast.zorder = None
        if not hasattr(ast, 'as_'):
            ast.as_ = None
            
        self.indent()
        self.write("show ")
        needs_space = self.print_imspec(ast.imspec)

        if self.paired_with:
            if needs_space:
                self.write(" ")
            self.write(f'with {self.paired_with}')
            self.paired_with = True

        # atl attribute: since 6.10
        if ast.atl is not None:
            self.write(":")
            self.print_atl(ast.atl)

    @dispatch(renpy.ast.ShowLayer)
    def print_showlayer(self, ast):
        self.indent()
        self.write(f'show layer {ast.layer}')

        if ast.at_list:
            self.write(f' at {", ".join(ast.at_list)}')

        if ast.atl is not None:
            self.write(":")
            self.print_atl(ast.atl)

    @dispatch(renpy.ast.Scene)
    def print_scene(self, ast):
        # 应用Ren'Py 8.4.0兼容性默认值
        if not hasattr(ast, 'atl'):
            ast.atl = None
        if not hasattr(ast, 'imspec'):
            ast.imspec = None
        if not hasattr(ast, 'layer'):
            ast.layer = None
        if not hasattr(ast, 'onlayer'):
            ast.onlayer = None
            
        self.indent()
        self.write("scene")

        if ast.imspec is None:
            if isinstance(ast.layer, str):
                self.write(f' onlayer {ast.layer}')
            needs_space = True
        else:
            self.write(" ")
            needs_space = self.print_imspec(ast.imspec)

        if self.paired_with:
            if needs_space:
                self.write(" ")
            self.write(f'with {self.paired_with}')
            self.paired_with = True

        # atl attribute: since 6.10
        if ast.atl is not None:
            self.write(":")
            self.print_atl(ast.atl)

    @dispatch(renpy.ast.Hide)
    def print_hide(self, ast):
        self.indent()
        self.write("hide ")
        needs_space = self.print_imspec(ast.imspec)
        if self.paired_with:
            if needs_space:
                self.write(" ")
            self.write(f'with {self.paired_with}')
            self.paired_with = True

    @dispatch(renpy.ast.With)
    def print_with(self, ast):
        # Apply defaults for Ren'Py 8.4.0 compatibility
        if not hasattr(ast, 'paired'):
            ast.paired = None
        if not hasattr(ast, 'expr'):
            ast.expr = None
            
        # 'paired'属性表示这个with
        # 和之后的with节点是后缀
        # with语句的一部分。检测这个并正确处理它
        if ast.paired is not None:
            # 健全性检查。检查是否有匹配的with语句在再往后两个节点
            if not (isinstance(self.block[self.index + 2], renpy.ast.With)
                    and self.block[self.index + 2].expr == ast.paired):
                raise Exception(f'Unmatched paired with {self.paired_with!r} != {ast.expr!r}')

            self.paired_with = ast.paired

        # paired_with属性自6.7.1以来
        elif self.paired_with:
            # 检查它是否被show/scene语句消耗了
            if self.paired_with is not True:
                self.write(f' with {ast.expr}')
            self.paired_with = False
        else:
            self.advance_to_line(ast.linenumber)
            self.indent()
            self.write(f'with {ast.expr}')
            self.paired_with = False

    @dispatch(renpy.ast.Camera)
    def print_camera(self, ast):
        self.indent()
        self.write("camera")

        # Apply defaults for Ren'Py 8.4.0 compatibility
        layer = getattr(ast, 'layer', 'master')
        if layer is None:
            layer = 'master'

        if layer != "master":
            self.write(f' {layer}')

        if ast.at_list:
            self.write(f' at {", ".join(ast.at_list)}')

        if ast.atl is not None:
            self.write(":")
            self.print_atl(ast.atl)

    # 流程控制

    @dispatch(renpy.ast.Label)
    def print_label(self, ast):
        # Apply defaults for Ren'Py 8.4.0 compatibility
        if not hasattr(ast, 'parameters'):
            ast.parameters = None
        if not hasattr(ast, 'name'):
            ast.name = getattr(ast, '_name', None)
        if not hasattr(ast, 'block'):
            ast.block = []
            
        # 如果一个Call块在我们之前，它把我们打印为"from"
        if (self.index and isinstance(self.block[self.index - 1], renpy.ast.Call)):
            return

        # 看看我们是否是菜单的标签，而不是独立的标签。
        if not ast.block and ast.parameters is None:
            remaining_blocks = len(self.block) - self.index
            if remaining_blocks > 1:
                # 标签后跟菜单
                next_ast = self.block[self.index + 1]
                if (isinstance(next_ast, renpy.ast.Menu)
                        and next_ast.linenumber == ast.linenumber):
                    self.label_inside_menu = ast
                    return

            if remaining_blocks > 2:
                # 标签，后跟一个say，然后是菜单
                next_next_ast = self.block[self.index + 2]
                if (isinstance(next_ast, renpy.ast.Say)
                        and isinstance(next_next_ast, renpy.ast.Menu)
                        and next_next_ast.linenumber == ast.linenumber
                        and self.say_belongs_to_menu(next_ast, next_next_ast)):

                    self.label_inside_menu = ast
                    return

        self.advance_to_line(ast.linenumber)
        self.indent()

        # 可能我们是"init label"，而不是常规标签。在我们解析子节点之前
        # 无法知道我们是否是，所以临时重定向我们的所有输出直到
        # 完成，这样如果我们是的话，我们可以挤入一个"init "
        out_file = self.out_file
        self.out_file = StringIO()
        missing_init = self.missing_init
        self.missing_init = False
        try:
            self.write(f'label {ast.name}{reconstruct_paraminfo(ast.parameters)}'
                       f'{" hide" if getattr(ast, "hide", False) else ""}:')
            self.print_nodes(ast.block, 1)
        finally:
            if self.missing_init:
                out_file.write("init ")
            self.missing_init = missing_init
            out_file.write(self.out_file.getvalue())
            self.out_file = out_file

    @dispatch(renpy.ast.Jump)
    def print_jump(self, ast):
        # Apply defaults for Ren'Py 8.4.0 compatibility
        if not hasattr(ast, 'target'):
            ast.target = None
        if not hasattr(ast, 'expression'):
            ast.expression = False
            
        self.indent()
        self.write(f'jump {"expression " if ast.expression else ""}{ast.target}')

    @dispatch(renpy.ast.Call)
    def print_call(self, ast):
        self.indent()
        words = WordConcatenator(False)
        words.append("call")
        if ast.expression:
            words.append("expression")
        words.append(ast.label)

        if ast.arguments is not None:
            if ast.expression:
                words.append("pass")
            words.append(reconstruct_arginfo(ast.arguments))

        # 我们不需要在这里检查是否有足够的元素，
        # 因为Label或Pass总是在Call之后发出。
        next_block = self.block[self.index + 1]
        if isinstance(next_block, renpy.ast.Label):
            words.append(f'from {next_block.name}')

        self.write(words.join())

    @dispatch(renpy.ast.Return)
    def print_return(self, ast):
        # Apply defaults for Ren'Py 8.4.0 compatibility
        if not hasattr(ast, 'expression'):
            ast.expression = None
            
        if (ast.expression is None
                and self.parent is None
                and self.index + 1 == len(self.block)
                and self.index
                and ast.linenumber == self.block[self.index - 1].linenumber):
            # 从Ren'Py提交356c6e34开始，每个rpyc文件末尾都会添加一个return语句。
            # 不要在源代码中包含这个。
            return

        self.advance_to_line(ast.linenumber)
        self.indent()
        self.write("return")

        if ast.expression is not None:
            self.write(f' {ast.expression}')

    @dispatch(renpy.ast.If)
    def print_if(self, ast):
        statement = First("if", "elif")

        # Apply defaults for Ren'Py 8.4.0 compatibility
        entries = getattr(ast, 'entries', [])
        if not entries:
        # 如果没有entries，跳过这个if语句
            return

        for i, (condition, block) in enumerate(entries):
            # Unicode字符串"True"用作else:的条件。
            # 但如果它是实际的表达式，它就是renpy.ast.PyExpr
            if (i + 1) == len(entries) and not isinstance(condition, renpy.ast.PyExpr):
                self.indent()
                self.write("else:")
            else:
                if (hasattr(condition, 'linenumber')):
                    self.advance_to_line(condition.linenumber)
                self.indent()
                self.write(f'{statement()} {condition}:')

            # 只有在块存在且不为空时才打印
            if block:
                self.print_nodes(block, 1)

    @dispatch(renpy.ast.While)
    def print_while(self, ast):
        self.indent()
        self.write(f'while {ast.condition}:')

        self.print_nodes(ast.block, 1)

    @dispatch(renpy.ast.Pass)
    def print_pass(self, ast):
        if (self.index and isinstance(self.block[self.index - 1], renpy.ast.Call)):
            return

        if (self.index > 1
                and isinstance(self.block[self.index - 2], renpy.ast.Call)
                and isinstance(self.block[self.index - 1], renpy.ast.Label)
                and self.block[self.index - 2].linenumber == ast.linenumber):
            return

        self.advance_to_line(ast.linenumber)
        self.indent()
        self.write("pass")

    def should_come_before(self, first, second):
        return first.linenumber < second.linenumber

    def require_init(self):
        if not self.in_init:
            self.missing_init = True

    def set_best_init_offset(self, nodes):
        votes = {}
        for ast in nodes:
            if not isinstance(ast, renpy.ast.Init):
                continue
            offset = ast.priority
            # Keep this block in sync with print_init
            if len(ast.block) == 1 and not self.should_come_before(ast, ast.block[0]):
                if isinstance(ast.block[0], renpy.ast.Screen):
                    offset -= -500
                elif isinstance(ast.block[0], renpy.ast.Testcase):
                    offset -= 500
                elif isinstance(ast.block[0], renpy.ast.Image):
                    offset -= 500
            votes[offset] = votes.get(offset, 0) + 1
        if votes:
            winner = max(votes, key=votes.get)
            # 只有在可以节省超过一个优先级规范时才值得设置init偏移
            if votes.get(0, 0) + 1 < votes[winner]:
                self.set_init_offset(winner)

    def set_init_offset(self, offset):
        def do_set_init_offset(linenumber):
            # 如果我们到达文件末尾并且还没有发出这个，
            # 不要费心了，因为它只适用于下面的内容。
            if linenumber is None or linenumber - self.linenumber <= 1 or self.indent_level:
                return True
            if offset != self.init_offset:
                self.indent()
                self.write(f'init offset = {offset}')
                self.init_offset = offset
            return False

        self.do_when_blank_line(do_set_init_offset)

    @dispatch(renpy.ast.Init)
    def print_init(self, ast):
        in_init = self.in_init
        self.in_init = True
        try:
            # A bunch of statements can have implicit init blocks
            # Define has a default priority of 0, screen of -500 and image of 990
            # 保持此块与set_best_init_offset同步
            # TODO merge this and require_init into another decorator or something
            if (len(ast.block) == 1
                    and (isinstance(ast.block[0], (renpy.ast.Define, renpy.ast.Default,
                                                   renpy.ast.Transform))
                         or (ast.priority == -500 + self.init_offset
                             and isinstance(ast.block[0], renpy.ast.Screen))
                         or (ast.priority == self.init_offset
                             and isinstance(ast.block[0], renpy.ast.Style))
                         or (ast.priority == 500 + self.init_offset
                             and isinstance(ast.block[0], renpy.ast.Testcase))
                         or (ast.priority == 0 + self.init_offset
                             and isinstance(ast.block[0], renpy.ast.UserStatement)
                             and ast.block[0].line.startswith("layeredimage "))
                         or (ast.priority == 500 + self.init_offset
                             and isinstance(ast.block[0], renpy.ast.Image)))
                    and not (self.should_come_before(ast, ast.block[0]))):
                # 如果它们满足这个条件，我们只是打印包含的语句
                self.print_nodes(ast.block)

            # translatestring语句被分开并放入init块中。
            elif (len(ast.block) > 0
                  and ast.priority == self.init_offset
                  and all(isinstance(i, renpy.ast.TranslateString) for i in ast.block)
                  and all(i.language == ast.block[0].language for i in ast.block[1:])):
                self.print_nodes(ast.block)

            else:
                self.indent()
                self.write("init")
                if ast.priority != self.init_offset:
                    self.write(f' {ast.priority - self.init_offset}')

                if len(ast.block) == 1 and not self.should_come_before(ast, ast.block[0]):
                    self.write(" ")
                    self.skip_indent_until_write = True
                    self.print_nodes(ast.block)
                else:
                    self.write(":")
                    self.print_nodes(ast.block, 1)
        finally:
            self.in_init = in_init

    def print_say_inside_menu(self):
        self.print_say(self.say_inside_menu, inmenu=True)
        self.say_inside_menu = None

    def print_menu_item(self, label, condition, block, arguments):
        self.indent()
        self.write(f'"{string_escape(label)}"')

        if arguments is not None:
            self.write(reconstruct_arginfo(arguments))

        if block is not None:
            # ren'py uses the unicode string "True" as condition when there isn't one.
            if isinstance(condition, renpy.ast.PyExpr):
                self.write(f' if {condition}')
            self.write(":")
            self.print_nodes(block, 1)

    @dispatch(renpy.ast.Menu)
    def print_menu(self, ast):
        self.indent()
        self.write("menu")
        if self.label_inside_menu is not None:
            self.write(f' {self.label_inside_menu.name}')
            self.label_inside_menu = None

        # arguments attribute added in 7.1.4
        if getattr(ast, "arguments", None) is not None:
            self.write(reconstruct_arginfo(ast.arguments))

        self.write(":")

        with self.increase_indent():
            # Apply defaults for Ren'Py 8.4.0 compatibility
            with_ = getattr(ast, 'with_', None)
            if with_ is not None:
                self.indent()
                self.write(f'with {with_}')

            set_ = getattr(ast, 'set', None)
            if set_ is not None:
                self.indent()
                self.write(f'set {set_}')

            # item_arguments attribute since 7.1.4
            if hasattr(ast, 'item_arguments'):
                item_arguments = ast.item_arguments
            else:
                item_arguments = [None] * len(ast.items)

            for (label, condition, block), arguments in zip(ast.items, item_arguments):
                if self.options.translator:
                    label = self.options.translator.strings.get(label, label)

                state = None

                # if the condition is a unicode subclass with a "linenumber" attribute it was
                # script.
                # If it isn't ren'py used to insert a "True" string. This string used to be of
                # type str but nowadays it's of type unicode, just not of type PyExpr
                # todo: this check probably doesn't work in ren'py 8
                if isinstance(condition, str) and hasattr(condition, "linenumber"):
                    if (self.say_inside_menu is not None
                            and condition.linenumber > self.linenumber + 1):
                        # The easy case: we know the line number that the menu item is on,
                        # because the condition tells us
                        # So we put the say statement here if there's room for it, or don't if
                        # there's not
                        self.print_say_inside_menu()
                    self.advance_to_line(condition.linenumber)
                elif self.say_inside_menu is not None:
                    # The hard case: we don't know the line number that the menu item is on
                    # 所以尝试把它放入，但如果这让我们在
                    # 行号上落后，准备撤销它
                    state = self.save_state()
                    self.most_lines_behind = self.last_lines_behind
                    self.print_say_inside_menu()

                self.print_menu_item(label, condition, block, arguments)

                if state is not None:
                    # state[7]是self.last_lines_behind的保存值
                    if self.most_lines_behind > state[7]:
                        # 我们试图打印菜单内的say语句，但它
                        # 不适合这里
                        # 撤销它并在没有它的情况下再次打印这个项目。我们稍后会把它放入
                        self.rollback_state(state)
                        self.print_menu_item(label, condition, block, arguments)
                    else:
                        # state[6]是self.most_lines_behind的保存值
                        self.most_lines_behind = max(state[6], self.most_lines_behind)
                        self.commit_state(state)

            if self.say_inside_menu is not None:
                # There was no room for this before any of the menu options, so it will just
                # have to go after them all
                self.print_say_inside_menu()

    # 编程相关函数

    @dispatch(renpy.ast.Python)
    def print_python(self, ast, early=False):
        self.indent()

        code = ast.code.source
        if code[0] == '\n':
            code = code[1:]
            self.write("python")
            if early:
                self.write(" early")
            if ast.hide:
                self.write(" hide")
            # store attribute added in 6.14
            if getattr(ast, "store", "store") != "store":
                self.write(" in ")
        # 去除前置的"store."
                self.write(ast.store[6:])
            self.write(":")

            with self.increase_indent():
                self.write_lines(split_logical_lines(code))

        else:
            self.write(f'$ {code}')

    @dispatch(renpy.ast.EarlyPython)
    def print_earlypython(self, ast):
        self.print_python(ast, early=True)

    @dispatch(renpy.ast.Define)
    def print_define(self, ast):
        self.require_init()
        self.indent()

        # If we have an implicit init block with a non-default priority, we need to store
        # the priority here.
        priority = ""
        if isinstance(self.parent, renpy.ast.Init):
            init = self.parent
            if (init.priority != self.init_offset
                    and len(init.block) == 1
                    and not self.should_come_before(init, ast)):
                priority = f' {init.priority - self.init_offset}'

        index = ""
        # index属性在7.4中添加
        if getattr(ast, "index", None) is not None:
            index = f'[{ast.index.source}]'

        # operator属性在7.4中添加
        operator = getattr(ast, "operator", "=")

        # store属性在6.18.2中添加
        if getattr(ast, "store", "store") == "store":
            self.write(f'define{priority} {ast.varname}{index} {operator} {ast.code.source}')
        else:
            self.write(
                f'define{priority} {ast.store[6:]}.{ast.varname}{index} {operator} '
                f'{ast.code.source}')

    @dispatch(renpy.ast.Default)
    def print_default(self, ast):
        self.require_init()
        self.indent()

        # Apply defaults for Ren'Py 8.4.0 compatibility
        store = getattr(ast, 'store', 'store')
        if store is None:
            store = 'store'

        # If we have an implicit init block with a non-default priority, we need to store the
        # priority here.
        priority = ""
        if isinstance(self.parent, renpy.ast.Init):
            init = self.parent
            if (init.priority != self.init_offset
                    and len(init.block) == 1
                    and not self.should_come_before(init, ast)):
                priority = f' {init.priority - self.init_offset}'

        if store == "store":
            self.write(f'default{priority} {ast.varname} = {ast.code.source}')
        else:
            self.write(f'default{priority} {store[6:]}.{ast.varname} = {ast.code.source}')

    # 特殊功能

    # 返回紧接在菜单语句之前的Say语句
    # 是否实际属于菜单语句内部。
    def say_belongs_to_menu(self, say, menu):
        # Apply defaults for Ren'Py 8.4.0 compatibility
        interact = getattr(say, 'interact', True)
        who = getattr(say, 'who', None)
        with_ = getattr(say, 'with_', None)
        attributes = getattr(say, 'attributes', None)
        
        return (not interact
                and who is not None
                and with_ is None
                and attributes is None
                and isinstance(menu, renpy.ast.Menu)
                and menu.items[0][2] is not None
                and not self.should_come_before(say, menu))

    @dispatch(renpy.ast.Say)
    def print_say(self, ast, inmenu=False):
        # Apply defaults for Ren'Py 8.4.0 compatibility
        if not hasattr(ast, 'who'):
            ast.who = None
        if not hasattr(ast, 'what'):
            ast.what = None
        if not hasattr(ast, 'with_'):
            ast.with_ = None
        if not hasattr(ast, 'interact'):
            ast.interact = True
        if not hasattr(ast, 'attributes'):
            ast.attributes = None
        if not hasattr(ast, 'temporary_attributes'):
            ast.temporary_attributes = None
        if not hasattr(ast, 'rollback'):
            ast.rollback = None
            
        # 如果这个say语句位于菜单语句之前，推迟发出它直到我们
        # 处理菜单
        if (not inmenu
                and self.index + 1 < len(self.block)
                and self.say_belongs_to_menu(ast, self.block[self.index + 1])):
            self.say_inside_menu = ast
            return

        # 否则就写出来。
        self.indent()
        self.write(say_get_code(ast, inmenu))

    @dispatch(renpy.ast.UserStatement)
    def print_userstatement(self, ast):
        self.indent()
        self.write(ast.line)

        # block属性自6.13.0以来
        if getattr(ast, "block", None):
            with self.increase_indent():
                self.print_lex(ast.block)

    def print_lex(self, lex):
        for file, linenumber, content, block in lex:
            self.advance_to_line(linenumber)
            self.indent()
            self.write(content)
            if block:
                with self.increase_indent():
                    self.print_lex(block)

    @dispatch(renpy.ast.Style)
    def print_style(self, ast):
        self.require_init()
        keywords = {ast.linenumber: WordConcatenator(False, True)}

        # Apply defaults for Ren'Py 8.4.0 compatibility
        parent = getattr(ast, 'parent', None)
        clear = getattr(ast, 'clear', False)
        take = getattr(ast, 'take', None)
        delattr = getattr(ast, 'delattr', [])

        # 这些不存储行号，所以只是把它们放在第一行
        if parent is not None:
            keywords[ast.linenumber].append(f'is {parent}')
        if clear:
            keywords[ast.linenumber].append("clear")
        if take is not None:
            keywords[ast.linenumber].append(f'take {take}')
        for delname in delattr:
            keywords[ast.linenumber].append(f'del {delname}')

        # 这些确实存储行号
        variant = getattr(ast, 'variant', None)
        properties = getattr(ast, 'properties', {})
        
        if variant is not None:
            if variant.linenumber not in keywords:
                keywords[variant.linenumber] = WordConcatenator(False)
            keywords[variant.linenumber].append(f'variant {variant}')
        for key, value in properties.items():
            if value.linenumber not in keywords:
                keywords[value.linenumber] = WordConcatenator(False)
            keywords[value.linenumber].append(f'{key} {value}')

        keywords = sorted([(k, v.join()) for k, v in keywords.items()],
                          key=itemgetter(0))
        self.indent()
        self.write(f'style {ast.style_name}')
        if keywords[0][1]:
            self.write(f' {keywords[0][1]}')
        if len(keywords) > 1:
            self.write(":")
            with self.increase_indent():
                for i in keywords[1:]:
                    self.advance_to_line(i[0])
                    self.indent()
                    self.write(i[1])

    # 翻译函数

    @dispatch(renpy.ast.Translate)
    def print_translate(self, ast):
        self.indent()
        self.write(f'translate {ast.language or "None"} {ast.identifier}:')

        self.print_nodes(ast.block, 1)

    @dispatch(renpy.ast.EndTranslate)
    def print_endtranslate(self, ast):
        # 一个隐式添加的节点，什么都不做...
        pass

    @dispatch(renpy.ast.TranslateString)
    def print_translatestring(self, ast):
        self.require_init()
        # 最后一个节点是translatestrings节点吗？
        if not (self.index
                and isinstance(self.block[self.index - 1], renpy.ast.TranslateString)
                and self.block[self.index - 1].language == ast.language):
            self.indent()
            self.write(f'translate {ast.language or "None"} strings:')

        # TranslateString的linenumber指向有"old"的行，而不是
        # 上面的"translate ... strings:"行
        with self.increase_indent():
            self.advance_to_line(ast.linenumber)
            self.indent()
            self.write(f'old "{string_escape(ast.old)}"')
            # newloc属性自6.99以来
            if hasattr(ast, "newloc"):
                self.advance_to_line(ast.newloc[1])
            self.indent()
            self.write(f'new "{string_escape(ast.new)}"')

    @dispatch(renpy.ast.TranslateBlock)
    @dispatch(renpy.ast.TranslateEarlyBlock)
    def print_translateblock(self, ast):
        self.indent()
        self.write(f'translate {ast.language or "None"} ')

        self.skip_indent_until_write = True

        in_init = self.in_init
        if (len(ast.block) == 1
                and isinstance(ast.block[0], (renpy.ast.Python, renpy.ast.Style))):
            # Ren'Py将"translate python"和"translate style"的TranslateBlock
            # 计算为Init。
            self.in_init = True
        try:
            self.print_nodes(ast.block)
        finally:
            self.in_init = in_init

    # 屏幕

    @dispatch(renpy.ast.Screen)
    def print_screen(self, ast):
        self.require_init()
        screen = ast.screen
        if isinstance(screen, renpy.screenlang.ScreenLangScreen):
            raise Exception(
                "Decompiling screen language version 1 screens is no longer supported. "
                "use the legacy branch of unrpyc if this is required"
            )

        if isinstance(screen, renpy.sl2.slast.SLScreen):
            self.linenumber = sl2decompiler.pprint(
                self.out_file, screen, self.options,
                self.indent_level, self.linenumber, self.skip_indent_until_write
            )
            self.skip_indent_until_write = False
        else:
            self.print_unknown(screen)

    # 测试用例

    @dispatch(renpy.ast.Testcase)
    def print_testcase(self, ast):
        self.require_init()
        self.indent()
        self.write(f'testcase {ast.label}:')
        self.linenumber = testcasedecompiler.pprint(
            self.out_file, ast.test.block, self.options,
            self.indent_level + 1, self.linenumber, self.skip_indent_until_write
        )
        self.skip_indent_until_write = False

    # Rpy python指令

    @dispatch(renpy.ast.RPY)
    def print_rpy_python(self, ast):
        self.indent()
        self.write(f'rpy python {ast.rest}')
