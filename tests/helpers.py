"""Loads single functions from plugin modules that import Alliance Auth."""
import ast
import pathlib

PKG = pathlib.Path(__file__).resolve().parent.parent / 'miningtax'


def load(relpath, names, namespace=None):
    """
    Executes only the named top-level functions, classes and assignments of a
    plugin module into `namespace` and returns it. The module itself is never
    imported, so its Alliance Auth imports don't have to resolve — whatever
    the loaded code needs is passed in through `namespace`.
    """
    namespace = {} if namespace is None else namespace
    tree = ast.parse((PKG / relpath).read_text(encoding='utf-8'))
    for node in tree.body:
        named = isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
        assigned = isinstance(node, ast.Assign) and any(getattr(t, 'id', None) in names for t in node.targets)
        if named or assigned:
            exec(compile(ast.Module([node], []), str(relpath), 'exec'), namespace)
    return namespace


def model_fields(relpath, class_name):
    """The field definitions (AST nodes) of a model class, by field name."""
    tree = ast.parse((PKG / relpath).read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    return {t.targets[0].id: t.value for t in cls.body if isinstance(t, ast.Assign)}
