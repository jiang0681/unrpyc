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

# 此模块包含一些特殊类和简写函数，用于支持renpy兼容性。
# 它们是分离的，这样就会有更少的代码重复，文件之间的依赖关系更简单，
# 以及避免文件中间的导入。

from . import magic
magic.fake_package("renpy")
import renpy  # noqa

import pickletools


# 这些命名类需要一些特殊处理，以便我们能够从pickle重建ren'py AST
SPECIAL_CLASSES = [set, frozenset]


# ren'py _令人烦恼地_即使在ren'py v8中仍然启用fix_imports，并且仍然默认使用pickle协议2。
# 所以set/frozenset被映射到错误的位置（__builtins__而不是builtins）
# 我们不想启用该选项，因为我们想控制pickler允许unpickle什么
# 所以在这里我们定义一些代理
class oldset(set):
    __module__ = "__builtin__"

    def __reduce__(self):
        cls, args, state = super().__reduce__()
        return (set, args, state)


oldset.__name__ = "set"
SPECIAL_CLASSES.append(oldset)


class oldfrozenset(frozenset):
    __module__ = "__builtin__"

    def __reduce__(self):
        cls, args, state = super().__reduce__()
        return (frozenset, args, state)


oldfrozenset.__name__ = "frozenset"
SPECIAL_CLASSES.append(oldfrozenset)


@SPECIAL_CLASSES.append
class PyExpr(magic.FakeStrict, str):
    __module__ = "renpy.astsupport"

    def __new__(cls, s, filename, linenumber, py=None, hash=None):
        self = str.__new__(cls, s)
        self.filename = filename
        self.linenumber = linenumber
        self.py = py
        self.hash = hash
        return self

    def __getnewargs__(self):
        if self.py is not None and self.hash is not None:
            return str(self), self.filename, self.linenumber, self.py, self.hash
        elif self.py is not None:
            return str(self), self.filename, self.linenumber, self.py
        else:
            return str(self), self.filename, self.linenumber


@SPECIAL_CLASSES.append
class PyCode(magic.FakeStrict):
    __module__ = "renpy.astsupport"

    def __setstate__(self, state):
        if len(state) == 4:
            (_, self.source, self.location, self.mode) = state
            self.py = None
        elif len(state) == 5:
            (_, self.source, self.location, self.mode, self.py) = state
        elif len(state) == 6:
            # Ren'Py 8.4.0 format with hash
            (_, self.source, self.location, self.mode, self.py, self.hash) = state
        else:
            # Fallback for any other number of parameters
            self.source = state[1] if len(state) > 1 else None
            self.location = state[2] if len(state) > 2 else None
            self.mode = state[3] if len(state) > 3 else None
            self.py = state[4] if len(state) > 4 else None
            self.hash = state[5] if len(state) > 5 else None
        self.bytecode = None


# Keep compatibility with older Ren'Py versions
@SPECIAL_CLASSES.append
class PyExpr(magic.FakeStrict, str):
    __module__ = "renpy.ast"

    def __new__(cls, s, filename, linenumber, py=None):
        self = str.__new__(cls, s)
        self.filename = filename
        self.linenumber = linenumber
        self.py = py
        return self

    def __getnewargs__(self):
        if self.py is not None:
            return str(self), self.filename, self.linenumber, self.py
        else:
            return str(self), self.filename, self.linenumber


@SPECIAL_CLASSES.append
class PyCode(magic.FakeStrict):
    __module__ = "renpy.ast"

    def __setstate__(self, state):
        if len(state) == 4:
            (_, self.source, self.location, self.mode) = state
            self.py = None
        elif len(state) == 5:
            (_, self.source, self.location, self.mode, self.py) = state
        elif len(state) == 6:
            # Ren'Py 8.4.0 format with hash
            (_, self.source, self.location, self.mode, self.py, self.hash) = state
        else:
            # Fallback for any other number of parameters
            self.source = state[1] if len(state) > 1 else None
            self.location = state[2] if len(state) > 2 else None
            self.mode = state[3] if len(state) > 3 else None
            self.py = state[4] if len(state) > 4 else None
            self.hash = state[5] if len(state) > 5 else None
        self.bytecode = None


@SPECIAL_CLASSES.append
class Sentinel(magic.FakeStrict):
    __module__ = "renpy.object"

    def __new__(cls, name):
        obj = object.__new__(cls)
        obj.name = name
        return obj


# These appear in the parsed contents of user statements.
@SPECIAL_CLASSES.append
class RevertableList(magic.FakeStrict, list):
    __module__ = "renpy.revertable"

    def __new__(cls):
        return list.__new__(cls)


@SPECIAL_CLASSES.append
class RevertableDict(magic.FakeStrict, dict):
    __module__ = "renpy.revertable"

    def __new__(cls):
        return dict.__new__(cls)


@SPECIAL_CLASSES.append
class RevertableSet(magic.FakeStrict, set):
    __module__ = "renpy.revertable"

    def __new__(cls):
        return set.__new__(cls)

    def __setstate__(self, state):
        if isinstance(state, tuple):
            self.update(state[0].keys())
        else:
            self.update(state)

# Before ren'py 7.5/8.0 they lived in renpy.python, so for compatibility we keep it here.
@SPECIAL_CLASSES.append
class RevertableList(magic.FakeStrict, list):
    __module__ = "renpy.python"

    def __new__(cls):
        return list.__new__(cls)


@SPECIAL_CLASSES.append
class RevertableDict(magic.FakeStrict, dict):
    __module__ = "renpy.python"

    def __new__(cls):
        return dict.__new__(cls)


@SPECIAL_CLASSES.append
class RevertableSet(magic.FakeStrict, set):
    __module__ = "renpy.python"

    def __new__(cls):
        return set.__new__(cls)

    def __setstate__(self, state):
        if isinstance(state, tuple):
            self.update(state[0].keys())
        else:
            self.update(state)


CLASS_FACTORY = magic.FakeClassFactory(SPECIAL_CLASSES, magic.FakeStrict)


def pickle_safe_loads(buffer: bytes):
    return magic.safe_loads(
        buffer, CLASS_FACTORY, {"collections"}, encoding="ASCII", errors="strict")


def pickle_safe_dumps(buffer: bytes):
    return magic.safe_dumps(buffer)


# if type hints: which one would be output file? bytesIO or bytes?
def pickle_safe_dump(buffer: bytes, outfile):
    return magic.safe_dump(buffer, outfile)


def pickle_loads(buffer: bytes):
    return magic.loads(buffer, CLASS_FACTORY)


def pickle_detect_python2(buffer: bytes):
    # When objects get pickled in protocol 2, python 2 will
    # normally emit BINSTRING/SHORT_BINSTRING opcodes for any attribute
    # names / binary strings.
    # protocol 2 in python 3 however, will never use BINSTRING/SHORT_BINSTRING
    # so presence of these opcodes is a tell that this file was not from renpy 8
    # even when recording a bytestring in python 3, it will not use BINSTRING/SHORT_BINSTRING
    # instead choosing to encode it into a BINUNICODE object
    #
    # caveat:
    # if a file uses `from __future__ import unicode_literals`
    # combined with __slots__ that are entered as plain "strings"
    # then attributes will use BINUNICODE instead (like py3)
    # Most ren'py AST classes do use __slots__ so that's a bit annoying

    for opcode, arg, pos in pickletools.genops(buffer):
        if opcode.code == "\x80":
            # from what I know ren'py for now always uses protocol 2,
            # but it might've been different in the past, and change in the future
            if arg < 2:
                return True

            elif arg > 2:
                return False

        if opcode.code in "TU":
            return True

    return False


# AST Default Values for Ren'Py 8.4.0 compatibility
AST_DEFAULT_VALUES = {
    'Label': {
        'name': None,
        'parameters': None,
        'block': [],
        '_name': None,  # 8.4.0 renamed name to _name
    },
    'Say': {
        'who': None,
        'what': None,
        'with_': None,
        'interact': True,
        'attributes': None,
        'temporary_attributes': None,
        'rollback': None,
    },
    'Menu': {
        'items': [],
        'set': None,
        'with_': None,
        'rollback': None,
    },
    'Show': {
        'imspec': None,
        'atl': None,
        'layer': None,
        'at_list': [],
        'onlayer': None,
        'behind': [],
        'zorder': None,
        'as_': None,
    },
    'Hide': {
        'imspec': None,
        'atl': None,
        'layer': None,
        'onlayer': None,
    },
    'Scene': {
        'imspec': None,
        'atl': None,
        'layer': None,
        'onlayer': None,
    },
    'Camera': {
        'layer': 'master',
        'at_list': [],
        'atl': None,
    },
    'With': {
        'expr': None,
        'paired': None,
    },
    'Jump': {
        'target': None,
        'expression': False,
    },
    'Call': {
        'label': None,
        'arguments': None,
        'expression': False,
        'from_current': False,
    },
    'Return': {
        'expression': None,
    },
    'If': {
        'entries': [],
    },
    'While': {
        'condition': None,
        'block': [],
    },
    'Pass': {},
    'Init': {
        'priority': 0,
        'block': [],
    },
    'Image': {
        'imgname': None,
        'code': None,
        'atl': None,
    },
    'Transform': {
        'varname': None,
        'parameters': None,
        'code': None,
        'atl': None,
    },
    'Python': {
        'code': None,
        'hide': False,
        'store': 'store',
    },
    'Default': {
        'varname': None,
        'code': None,
        'store': 'store',
    },
    'Style': {
        'style_name': None,
        'parent': None,
        'properties': {},
        'clear': False,
        'take': None,
        'delattr': [],
        'variant': None,
    },
    'UserStatement': {
        'line': None,
        'block': [],
        'parsed': None,
    },
}

def apply_ast_defaults(ast_obj, ast_type):
    """Apply default values to AST objects for Ren'Py 8.4.0 compatibility."""
    if ast_type in AST_DEFAULT_VALUES:
        defaults = AST_DEFAULT_VALUES[ast_type]
        for attr, default_value in defaults.items():
            if not hasattr(ast_obj, attr):
                setattr(ast_obj, attr, default_value)
    return ast_obj


def fix_ast_for_renpy_84(stmts):
    """Fix AST objects for Ren'Py 8.4.0 compatibility by applying default values."""
    if not stmts:
        return stmts
    
    def fix_ast_node(node):
        """Recursively fix an AST node and its children."""
        if hasattr(node, '__class__'):
            class_name = node.__class__.__name__
            apply_ast_defaults(node, class_name)
        
        # Handle special attribute mapping for Label class
        if hasattr(node, '__class__') and node.__class__.__name__ == 'Label':
            if hasattr(node, '_name') and not hasattr(node, 'name'):
                node.name = node._name
        
        # Recursively fix child nodes
        if hasattr(node, 'block') and isinstance(node.block, list):
            for child in node.block:
                fix_ast_node(child)
        
        if hasattr(node, 'entries') and isinstance(node.entries, list):
            for entry in node.entries:
                if isinstance(entry, tuple) and len(entry) >= 3:
                    # Menu entries: (condition, label, block)
                    if entry[2]:  # block
                        for child in entry[2]:
                            fix_ast_node(child)
        
        if hasattr(node, 'items') and isinstance(node.items, list):
            for item in node.items:
                if isinstance(item, tuple) and len(item) >= 3:
                    # Menu items: (label, condition, block)
                    if item[2]:  # block
                        for child in item[2]:
                            fix_ast_node(child)
        
        return node
    
    # Fix all statements in the list
    for stmt in stmts:
        fix_ast_node(stmt)
    
    return stmts


def fix_ast_for_renpy_84(ast_nodes):
    """
    Fix AST nodes for Ren'Py 8.4.0 compatibility by adding missing attributes.
    """
    if not isinstance(ast_nodes, list):
        ast_nodes = [ast_nodes]
    
    for node in ast_nodes:
        node_type = type(node).__name__
        
        # Apply defaults based on node type
        if node_type == 'Label':
            if not hasattr(node, 'parameters'):
                node.parameters = None
            if not hasattr(node, 'name'):
                node.name = getattr(node, '_name', None)
            if not hasattr(node, 'block'):
                node.block = []
                
        elif node_type == 'Scene':
            if not hasattr(node, 'atl'):
                node.atl = None
            if not hasattr(node, 'imspec'):
                node.imspec = None
            if not hasattr(node, 'layer'):
                node.layer = None
            if not hasattr(node, 'onlayer'):
                node.onlayer = None
                
        elif node_type == 'Show':
            if not hasattr(node, 'imspec'):
                node.imspec = None
            if not hasattr(node, 'atl'):
                node.atl = None
            if not hasattr(node, 'layer'):
                node.layer = None
            if not hasattr(node, 'at_list'):
                node.at_list = []
            if not hasattr(node, 'onlayer'):
                node.onlayer = None
            if not hasattr(node, 'behind'):
                node.behind = []
            if not hasattr(node, 'zorder'):
                node.zorder = None
            if not hasattr(node, 'as_'):
                node.as_ = None
                
        elif node_type == 'Hide':
            if not hasattr(node, 'imspec'):
                node.imspec = None
            if not hasattr(node, 'atl'):
                node.atl = None
            if not hasattr(node, 'layer'):
                node.layer = None
            if not hasattr(node, 'onlayer'):
                node.onlayer = None
                
        elif node_type == 'Say':
            if not hasattr(node, 'who'):
                node.who = None
            if not hasattr(node, 'what'):
                node.what = None
            if not hasattr(node, 'with_'):
                node.with_ = None
            if not hasattr(node, 'interact'):
                node.interact = True
            if not hasattr(node, 'attributes'):
                node.attributes = None
            if not hasattr(node, 'temporary_attributes'):
                node.temporary_attributes = None
            if not hasattr(node, 'rollback'):
                node.rollback = None
                
        elif node_type == 'Menu':
            if not hasattr(node, 'items'):
                node.items = []
            if not hasattr(node, 'set'):
                node.set = None
            if not hasattr(node, 'with_'):
                node.with_ = None
            if not hasattr(node, 'rollback'):
                node.rollback = None
                
        elif node_type == 'With':
            if not hasattr(node, 'expr'):
                node.expr = None
            if not hasattr(node, 'paired'):
                node.paired = None
                
        elif node_type == 'Jump':
            if not hasattr(node, 'target'):
                node.target = None
            if not hasattr(node, 'expression'):
                node.expression = False
                
        elif node_type == 'Call':
            if not hasattr(node, 'label'):
                node.label = None
            if not hasattr(node, 'arguments'):
                node.arguments = None
            if not hasattr(node, 'expression'):
                node.expression = False
            if not hasattr(node, 'from_current'):
                node.from_current = False
                
        elif node_type == 'Return':
            if not hasattr(node, 'expression'):
                node.expression = None
                
        elif node_type == 'If':
            if not hasattr(node, 'entries'):
                node.entries = []
                
        elif node_type == 'While':
            if not hasattr(node, 'condition'):
                node.condition = None
            if not hasattr(node, 'block'):
                node.block = []
                
        elif node_type == 'Init':
            if not hasattr(node, 'priority'):
                node.priority = 0
            if not hasattr(node, 'block'):
                node.block = []
                
        elif node_type == 'Image':
            if not hasattr(node, 'imgname'):
                node.imgname = None
            if not hasattr(node, 'code'):
                node.code = None
            if not hasattr(node, 'atl'):
                node.atl = None
                
        elif node_type == 'Transform':
            if not hasattr(node, 'varname'):
                node.varname = None
            if not hasattr(node, 'parameters'):
                node.parameters = None
            if not hasattr(node, 'code'):
                node.code = None
            if not hasattr(node, 'atl'):
                node.atl = None
                
        elif node_type == 'Python':
            if not hasattr(node, 'code'):
                node.code = None
            if not hasattr(node, 'hide'):
                node.hide = False
            if not hasattr(node, 'store'):
                node.store = 'store'
                
        elif node_type == 'UserStatement':
            if not hasattr(node, 'line'):
                node.line = None
            if not hasattr(node, 'block'):
                node.block = []
            if not hasattr(node, 'parsed'):
                node.parsed = None
        
        # Recursively fix nested blocks
        if hasattr(node, 'block') and node.block:
            fix_ast_for_renpy_84(node.block)
    
    return ast_nodes
