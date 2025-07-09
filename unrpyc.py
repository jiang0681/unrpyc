__title__ = "Unrpyc"
__version__ = 'v2.0.2c'
__url__ = "https://github.com/jiang0681/unrpyc/"


import argparse
import glob
import struct
import sys
import traceback
import zlib
from pathlib import Path

try:
    from multiprocessing import Pool, cpu_count
except ImportError:
    # 当多进程不可用时提供必要的模拟支持
    def cpu_count():
        return 1

import decompiler
import deobfuscate
from decompiler import astdump, translate
from decompiler.renpycompat import (pickle_safe_loads, pickle_safe_dumps, pickle_loads,
                                    pickle_detect_python2)


class Context:
    def __init__(self):
        # 要打印的日志行列表
        self.log_contents = []

        # 发生的任何异常
        self.error = None

        # 遇到的情况状态
        # 选项:
        #     error:      (默认) 抛出了意外异常
        #     ok:         过程成功完成
        #     bad_header: 给定文件无法解析为正常的rpyc文件
        #     skip:       由于已存在输出文件，跳过了给定文件
        self.state = "error"

        # 来自worker的返回值，如果有的话
        self.value = None

    def log(self, message):
        self.log_contents.append(message)

    def set_error(self, error):
        self.error = error

    def set_result(self, value):
        self.value = value

    def set_state(self, state):
        self.state = state


class BadRpycException(Exception):
    """当我们无法解析rpyc存档格式时抛出的异常"""
    pass


# API

def read_ast_from_file(in_file, context):
    # 读取rpyc v1或v2文件
    # v1文件只是一个包含一些数据和ast的zlib压缩pickle blob
    # v2文件包含一个基本的存档结构，可以解析以找到相同的blob
    raw_contents = in_file.read()
    file_start = raw_contents[:50]
    is_rpyc_v1 = False

    if not raw_contents.startswith(b"RENPY RPC2"):
        # 如果头部不存在，它应该是RPYC V1文件，只是blob
        contents = raw_contents
        is_rpyc_v1 = True

    else:
        # 解析存档结构
        position = 10
        chunks = {}
        have_errored = False

        for expected_slot in range(1, 0xFFFFFFFF):
            slot, start, length = struct.unpack("III", raw_contents[position: position + 12])

            if slot == 0:
                break

            if slot != expected_slot and not have_errored:
                have_errored = True

                context.log(
                    "警告: 遇到意外的槽位结构。文件头部结构可能已被更改。")

            position += 12

            chunks[slot] = raw_contents[start: start + length]

        if 1 not in chunks:
            context.set_state('bad_header')
            raise BadRpycException(
                "无法从rpyc文件中找到正确的槽位进行加载。文件头部结构已被更改。"
                f"文件头部: {file_start}")

        contents = chunks[1]

    try:
        contents = zlib.decompress(contents)
    except Exception:
        context.set_state('bad_header')
        raise BadRpycException(
            "在期望的位置未找到zlib压缩的blob。要么头部已被修改，要么文件结构已被更改。"
            f"文件头部: {file_start}") from None

    # 添加对ren'py 7文件的一些检测
    if is_rpyc_v1 or pickle_detect_python2(contents):
        version = "6" if is_rpyc_v1 else "7"

        context.log(
            "警告: 分析发现此.rpyc文件由ren'py版本 "
           f'{version} 或更低版本生成，而此unrpyc版本针对ren\'py '
            "版本8。仍将尝试反编译，但可能会出现错误或不正确的反编译。")

    _, stmts = pickle_safe_loads(contents)
    
    # 应用Ren'Py 8.4.0兼容性修复
    from decompiler.renpycompat import fix_ast_for_renpy_84
    stmts = fix_ast_for_renpy_84(stmts)
    
    return stmts


def get_ast(in_file, try_harder, context):
    """
    打开路径in_file处的rpyc文件以加载包含的AST。
    如果try_harder为True，将尝试绕过混淆技术。
    否则，将其作为普通rpyc文件加载。
    """
    with in_file.open('rb') as in_file:
        if try_harder:
            ast = deobfuscate.read_ast(in_file, context)
        else:
            ast = read_ast_from_file(in_file, context)
    return ast


def decompile_rpyc(input_filename, context, overwrite=False, try_harder=False, dump=False,
                   comparable=False, no_pyexpr=False, translator=None, init_offset=False,
                   sl_custom_names=None):

    # 输出文件名是输入文件名但扩展名为.rpy
    if dump:
        ext = '.txt'
    elif input_filename.suffix == ('.rpyc'):
        ext = '.rpy'
    elif input_filename.suffix == ('.rpymc'):
        ext = '.rpym'
    out_filename = input_filename.with_suffix(ext)


    if not overwrite and out_filename.exists():
        context.log(f'跳过 {input_filename}。{out_filename.name} 已存在。')
        context.set_state('skip')
        return

    context.log(f'正在反编译 {input_filename} 到 {out_filename.name} ...')
    ast = get_ast(input_filename, try_harder, context)

    with out_filename.open('w', encoding='utf-8') as out_file:
        if dump:
            astdump.pprint(out_file, ast, comparable=comparable, no_pyexpr=no_pyexpr)
        else:
            options = decompiler.Options(log=context.log_contents, translator=translator,
                                         init_offset=init_offset, sl_custom_names=sl_custom_names)

            decompiler.pprint(out_file, ast, options)

    context.set_state('ok')


def worker_tl(arg_tup):
    """
    此文件实现翻译功能的第一遍。它从给定的rpyc文件中收集TL数据，
    供通用worker在反编译时用于翻译。
    arg_tup是(args, filename)。在context中返回收集的TL数据。
    """
    args, filename = arg_tup
    context = Context()

    try:
        context.log(f'正在从 {filename} 提取翻译...')
        ast = get_ast(filename, args.try_harder, context)

        tl_inst = translate.Translator(args.translate, True)
        tl_inst.translate_dialogue(ast)

        # 此对象必须发送回主进程，为此需要进行pickle。
        # 默认的pickler无法正确pickle伪类，因此在此处手动处理。
        context.set_result(pickle_safe_dumps((tl_inst.dialogue, tl_inst.strings)))
        context.set_state("ok")

    except Exception as e:
        context.set_error(e)
        context.log(f'从 {filename} 提取翻译时出错:')
        context.log(traceback.format_exc())

    return context


def worker_common(arg_tup):
    """
    unrpyc的核心。arg_tup是(args, filename)。此worker将解压filename处的文件，
    反编译它，并将输出写入相应的rpy文件。
    """

    args, filename = arg_tup
    context = Context()

    if args.translator:
        args.translator = pickle_loads(args.translator)

    try:
        decompile_rpyc(
            filename, context, overwrite=args.clobber, try_harder=args.try_harder,
            dump=args.dump, no_pyexpr=args.no_pyexpr, comparable=args.comparable,
            init_offset=args.init_offset, sl_custom_names=args.sl_custom_names,
            translator=args.translator)

    except Exception as e:
        context.set_error(e)
        context.log(f'反编译 {filename} 时出错:')
        context.log(traceback.format_exc())

    return context


def run_workers(worker, common_args, private_args, parallelism):
    """
    使用多进程并行运行worker，最多使用`parallelism`个进程。
    Workers被调用为worker((common_args, private_args[i]))。
    Workers应该返回`Context`的实例作为返回值。
    """

    worker_args = ((common_args, x) for x in private_args)

    results = []
    if parallelism > 1:
        with Pool(parallelism) as pool:
            for result in pool.imap(worker, worker_args, 1):
                results.append(result)

                for line in result.log_contents:
                    print(line)

                print("")

    else:
        for result in map(worker, worker_args):
            results.append(result)

            for line in result.log_contents:
                print(line)

            print("")

    return results


def parse_sl_custom_names(unparsed_arguments):
    # 解析格式为classname=name-nchildren的字符串列表
    # 转换为{classname: (name, nchildren)}
    parsed_arguments = {}
    for argument in unparsed_arguments:
        content = argument.split("=")
        if len(content) != 2:
            raise Exception(f'自定义sl可显示对象注册中的格式错误: "{argument}"')

        classname, name = content
        split = name.split("-")
        if len(split) == 1:
            amount = "many"

        elif len(split) == 2:
            name, amount = split
            if amount == "0":
                amount = 0
            elif amount == "1":
                amount = 1
            elif amount == "many":
                pass
            else:
                raise Exception(
                    f'自定义sl可显示对象注册中的子节点数量错误: "{argument}"')

        else:
            raise Exception(
                f'自定义sl可显示对象注册中的格式错误: "{argument}"')

        parsed_arguments[classname] = (name, amount)

    return parsed_arguments


def plural_s(n, unit):
    """当'n'不是1时正确使用'unit'的复数形式"""
    return f"1 {unit}" if n == 1 else f"{n} {unit}s"


def main():
    if not sys.version_info[:2] >= (3, 9):
        raise Exception(
            f"'{__title__} {__version__}' 必须使用Python 3.9或更高版本执行。\n"
            f"您正在运行 {sys.version}")

    # argparse用法: python3 unrpyc.py [-c] [--try-harder] [-d] [-p] file [file ...]
    cc_num = cpu_count()
    ap = argparse.ArgumentParser(description="反编译 .rpyc/.rpymc 文件")

    ap.add_argument(
        'file',
        type=str,
        nargs='+',
        help="要反编译的文件名。"
        "传递的任何子目录/目录中的所有.rpyc文件也将被反编译。")

    ap.add_argument(
        '-c',
        '--clobber',
        dest='clobber',
        action='store_true',
        help="如果输出文件已存在，则覆盖它们。")

    ap.add_argument(
        '--try-harder',
        dest="try_harder",
        action="store_true",
        help="尝试一些针对常见混淆方法的变通方案。这会慢很多。")

    ap.add_argument(
        '-p',
        '--processes',
        dest='processes',
        action='store',
        type=int,
        choices=list(range(1, cc_num)),
        default=cc_num - 1 if cc_num > 2 else 1,
        help="使用指定数量的进程进行反编译。"
        "默认为可用硬件线程数减一，当多进程不可用时禁用。")

    astdump = ap.add_argument_group('astdump选项', '所有与ast转储相关的unrpyc选项。')
    astdump.add_argument(
        '-d',
        '--dump',
        dest='dump',
        action='store_true',
        help="不进行反编译，而是将ast美化打印到文件")

    astdump.add_argument(
        '--comparable',
        dest='comparable',
        action='store_true',
        help="仅用于转储，在比较转储时移除几种虚假差异。"
        "这会抑制即使代码相同也不同的属性，例如文件修改时间。")

    astdump.add_argument(
        '--no-pyexpr',
        dest='no_pyexpr',
        action='store_true',
        help="仅用于转储，禁用对PyExpr对象的特殊处理，改为将其打印为字符串。"
        "这在比较不同Ren'Py版本的转储时很有用。只有在必要时才应使用，"
        "因为它会导致信息丢失，如行号。")

    ap.add_argument(
        '--no-init-offset',
        dest='init_offset',
        action='store_false',
        help="默认情况下，unrpyc会尝试猜测何时使用了init offset语句并插入它们。"
        "这对于ren'py 8总是安全的，但由于基于启发式，可以禁用。"
        "生成的代码在功能上完全等价，只是稍微更杂乱。")

    ap.add_argument(
        '--register-sl-displayable',
        dest="sl_custom_names",
        type=str,
        nargs='+',
        help="接受由'='分隔的映射，"
        "其中第一个参数是用户定义的可显示对象的名称，"
        "第二个参数是包含可显示对象名称的字符串，"
        "可能后跟一个'-'，以及可显示对象接受的子项数量"
        "（有效选项是'0'、'1'或'many'，默认为'many'）")

    ap.add_argument(
        '-t',
        '--translate',
        dest='translate',
        type=str,
        action='store',
        help="使用tl目录中已存在的翻译更改反编译脚本文件中的对话语言。")

    ap.add_argument(
        '--version',
        action='version',
        version=f"{__title__} {__version__}")

    args = ap.parse_args()

    # 捕获不可能的参数组合，以免产生奇怪的错误或静默失败
    if (args.no_pyexpr or args.comparable) and not args.dump:
        ap.error("选项 '--comparable' 和 '--no_pyexpr' 需要 '--dump'。")

    if args.dump and args.translate:
        ap.error("选项 '--translate' 和 '--dump' 不能同时使用。")

    if args.sl_custom_names is not None:
        try:
            args.sl_custom_names = parse_sl_custom_names(args.sl_custom_names)
        except Exception as e:
            print("\n".join(e.args))
            return

    def glob_or_complain(inpath):
        """展开通配符并将输出转换为路径类似状态。"""
        retval = [Path(elem).resolve(strict=True) for elem in glob.glob(inpath, recursive=True)]
        if not retval:
            print(f'找不到输入路径: {inpath}')
        return retval

    def traverse(inpath):
        """
        从输入路径筛选rpyc/rpymc文件并返回它们。通过调用自身递归进入所有给定目录。
        """
        if inpath.is_file() and inpath.suffix in ['.rpyc', '.rpymc']:
            yield inpath
        elif inpath.is_dir():
            for item in inpath.iterdir():
                yield from traverse(item)

    # 通过globing和pathlib检查来自argparse的路径。构造一个包含所有
    # `Ren'Py编译文件`的任务列表，该应用程序被分配处理。
    worklist = []
    for entry in args.file:
        for globitem in glob_or_complain(entry):
            for elem in traverse(globitem):
                worklist.append(elem)

    # 检查我们是否确实有文件。不用担心没有传递参数，
    # 因为ArgumentParser会捕获这种情况
    if not worklist:
        print("找不到要反编译的脚本文件。")
        return

    if args.processes > len(worklist):
        args.processes = len(worklist)

    print(f"找到 {plural_s(len(worklist), 'file')} 要处理。"
          f"使用 {plural_s(args.processes, 'worker')} 执行反编译。")

    # 如果大文件在接近尾声时开始，可能会有很长时间只有一个线程在运行，
    # 这是低效的。通过首先启动大文件来避免这种情况。
    worklist.sort(key=lambda x: x.stat().st_size, reverse=True)

    translation_errors = 0
    args.translator = None
    if args.translate:
        # 对于翻译，我们首先需要分析所有文件的翻译数据。
        # 然后我们将所有这些收集回主进程，并构建一个
        # 包含所有这些的数据结构。然后将此数据结构传递给
        # 所有反编译进程。
        # 注意：因为此数据包含一些FakeClasses，多进程无法
        # 在进程之间传递它（它会pickle它们，pickle会抱怨
        # 这些）。因此，我们需要手动pickle和unpickle它。

        print("步骤1: 分析文件以获取翻译。")
        results = run_workers(worker_tl, args, worklist, args.processes)

        print('编译提取的翻译。')
        tl_dialogue = {}
        tl_strings = {}
        for entry in results:
            if entry.state != "ok":
                translation_errors += 1

            if entry.value:
                new_dialogue, new_strings = pickle_loads(entry.value)
                tl_dialogue.update(new_dialogue)
                tl_strings.update(new_strings)

        translator = translate.Translator(None)
        translator.dialogue = tl_dialogue
        translator.strings = tl_strings
        args.translator = pickle_safe_dumps(translator)

        print("步骤2: 反编译。")

    results = run_workers(worker_common, args, worklist, args.processes)

    success = sum(result.state == "ok" for result in results)
    skipped = sum(result.state == "skip" for result in results)
    failed = sum(result.state == "error" for result in results)
    broken = sum(result.state == "bad_header" for result in results)

    print("")
    print(f"{55 * '-'}")
    print(f"{__title__} {__version__} 结果摘要:")
    print(f"{55 * '-'}")
    print(f"处理了 {plural_s(len(results), 'file')}。")

    print(f"> {plural_s(success, 'file')} 成功反编译。")

    if broken:
        print(f"> {plural_s(broken, 'file')} 没有正确的头部，"
              "这些被忽略。")

    if failed:
        print(f"> {plural_s(failed, 'file')} 由于错误反编译失败。")

    if skipped:
        print(f"> {plural_s(skipped, 'file')} 被跳过，因为输出文件已存在。")

    if translation_errors:
        print(f"> {plural_s(translation_errors, 'file')} 翻译提取失败。")


    if skipped:
        print("")
        print("要覆盖现有文件而不是跳过它们，请使用 --clobber 标志。")

    if broken:
        print("")
        print("要尝试绕过对文件头部的修改，请使用 --try-harder 标志。")

    if failed:
        print("")
        print("反编译过程中遇到错误。查看日志以获取更多信息。")
        print("在制作错误报告时，请包含此完整日志。")

if __name__ == '__main__':
    main()
