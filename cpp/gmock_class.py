#!/usr/bin/env python
#
# Copyright 2008 Google Inc.  All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generate Google Mock classes from base classes.

This program will read in a C++ source file and output the Google Mock
classes for the specified classes.  If no class is specified, all
classes in the source file are emitted.

Usage:
  gmock_class.py header-file.h [ClassName]...

Output is sent to stdout.
"""

__author__ = 'nnorwitz@google.com (Neal Norwitz)'


import os
import re
import sys

from cpp import ast
from cpp import utils

# Preserve compatibility with Python 2.3.
try:
  _dummy = set
except NameError:
  import sets
  set = sets.Set

_VERSION = (1, 0, 1)  # The version of this script.
# How many spaces to indent.  Can set me with the INDENT environment variable.
_INDENT = 2


def _GenerateDestructor(output_lines, class_name):
  indent = ' ' * _INDENT
  mock_lines = [
    "%sMOCK_METHOD0(Die, void());" % indent,
    "%svirtual ~%s() { Die(); }" % (indent, class_name)
  ]
  for line in mock_lines:
    output_lines.append(line)


def GenerateReturnType(node):
    result = node.name
    template_args = [GenerateReturnType(arg) for arg in node.templated_types]
    if template_args:
      result += '<' + ', '.join(template_args) + '>'
    return result


def _GenerateMethods(output_lines, source, class_node):
  function_type = (ast.FUNCTION_VIRTUAL | ast.FUNCTION_PURE_VIRTUAL |
                   ast.FUNCTION_OVERRIDE)
  ctor_or_dtor = ast.FUNCTION_CTOR | ast.FUNCTION_DTOR
  indent = ' ' * _INDENT

  for node in class_node.body:
    # We only care about virtual functions.
    if (isinstance(node, ast.Function) and
        node.modifiers & function_type and
        not node.modifiers & ctor_or_dtor):
      # Pick out all the elements we need from the original function.
      const = ''
      if node.modifiers & ast.FUNCTION_CONST:
        const = 'CONST_'
      return_type = 'void'
      if node.return_type:
        # Add modifiers like 'const'.
        modifiers = ''
        if node.return_type.modifiers:
          modifiers = ' '.join(node.return_type.modifiers) + ' '
        return_type = modifiers + GenerateReturnType(node.return_type)
        if len(node.return_type.templated_types) > 1:
          for line in [
              '// The following line won\'t really compile, as the return',
              '// type has multiple template arguments.  To fix it, use a',
              '// typedef for the return type.']:
            output_lines.append(indent + line)
        if node.return_type.pointer:
          return_type += '*'
        if node.return_type.reference:
          return_type += '&'
        num_parameters = len(node.parameters)
        if len(node.parameters) == 1:
          first_param = node.parameters[0]
          if source[first_param.start:first_param.end].strip() == 'void':
            # We must treat T(void) as a function with no parameters.
            num_parameters = 0
      tmpl = ''
      if class_node.templated_types:
        tmpl = '_T'
      mock_method_macro = 'MOCK_%sMETHOD%d%s' % (const, num_parameters, tmpl)

      args = ''
      if node.parameters:
        args_list = []
        for param in node.parameters:
          param_string = source[param.start:param.end]
          # Remove // comments.
          param_string = re.sub(r'//.*', '', param_string)
          # Remove default value
          if param.default:
            pos = param_string.find("=")
            assert pos >= 0
            param_string = param_string[:pos]
          args_list.append(param_string.strip())
        args_strings = ', '.join(args_list)
        # Condense multiple spaces and eliminate newlines putting the
        # parameters together on a single line.  Ensure there is a
        # space in an argument which is split by a newline without
        # intervening whitespace, e.g.: int\nBar
        args = re.sub('  +', ' ', args_strings.replace('\n', ' '))

      # TODO: make formatting configurable
      # Create the mock method definition.
      output_lines.extend(['%s%s(%s, %s(%s));' % (indent, mock_method_macro, node.name, return_type, args)])


def is_interface(node):
    if not isinstance(node, ast.Class) or not node.body:
        return False

    class_node = node

    method_count = 0
    for sub_node in class_node.body:
        if isinstance(sub_node, ast.Function):
            modifiers = sub_node.modifiers
            is_pure_virtual = (modifiers & ast.FUNCTION_PURE_VIRTUAL) > 0
            is_virtual_destructor = (modifiers & (ast.FUNCTION_DTOR | ast.FUNCTION_VIRTUAL)) > 0
            is_constructor = (modifiers & ast.FUNCTION_CTOR) > 0
            if not is_pure_virtual and not is_virtual_destructor:
                return False
            if is_constructor:
              return False
            if is_pure_virtual:
                method_count += 1

    # at least one function is pure virtual
    if method_count == 0:
        return False

    return True


def _GenerateMocks(filename, source, ast_list, desired_class_names, mock_destructors):
  processed_class_names = set()
  lines = []
  for node in ast_list:
    # desired_class_names being None means that all classes are selected.
    if is_interface(node) and (not desired_class_names or node.name in desired_class_names):
      class_name = node.name
      parent_name = class_name
      processed_class_names.add(class_name)
      class_node = node
      # Add namespace before the class.
      if class_node.namespace:
        lines.extend(['namespace %s {' % n for n in class_node.namespace])  # }
        lines.append('')

      # Add template args for templated classes.
      if class_node.templated_types:
        # TODO(paulchang): The AST doesn't preserve template argument order,
        # so we have to make up names here.
        # TODO(paulchang): Handle non-type template arguments (e.g.
        # template<typename T, int N>).
        template_arg_count = len(class_node.templated_types.keys())
        template_args = ['T%d' % n for n in range(template_arg_count)]
        template_decls = ['typename ' + arg for arg in template_args]
        lines.append('template <' + ', '.join(template_decls) + '>')
        parent_name += '<' + ', '.join(template_args) + '>'

      # Add the class prolog.
      mock_class_name = "Mock%s" % class_name;
      lines.append('class %s : public %s {'  # }
                   % (mock_class_name, parent_name))
      lines.append('%spublic:' % (' ' * (_INDENT // 2)))

      # Add all the methods.
      _GenerateMethods(lines, source, class_node)

      if mock_destructors:
        _GenerateDestructor(lines, mock_class_name)

      # Close the class.
      if lines:
        # If there are no virtual methods, no need for a public label.
        if len(lines) == 2:
          del lines[-1]

        # Only close the class if there really is a class.
        lines.append('};')
        lines.append('')  # Add an extra newline.

      # Close the namespace.
      if class_node.namespace:
        for i in range(len(class_node.namespace)-1, -1, -1):
          lines.append('}  // namespace %s' % class_node.namespace[i])
        lines.append('')  # Add an extra newline.

  if desired_class_names:
    missing_class_name_list = list(desired_class_names - processed_class_names)
    if missing_class_name_list:
      missing_class_name_list.sort()
      sys.stderr.write('Class(es) not found in %s: %s\n' %
                       (filename, ', '.join(missing_class_name_list)))
  elif not processed_class_names:
    sys.stderr.write('No class found in %s\n' % filename)

  return lines


def main(argv=sys.argv):
  if len(argv) < 2:
    sys.stderr.write('Google Mock Class Generator v%s\n\n' %
                     '.'.join(map(str, _VERSION)))
    sys.stderr.write(__doc__)
    return 1

  global _INDENT
  try:
    _INDENT = int(os.environ['INDENT'])
  except KeyError:
    pass
  except:
    sys.stderr.write('Unable to use indent of %s\n' % os.environ.get('INDENT'))

  filename = argv[1]
  mock_destructors = False
  desired_class_names = None  # None means all classes in the source file.
  if len(argv) >= 3:
    desired_classes_start = 2
    if argv[2] == "-d":
      desired_classes_start += 1
      mock_destructors = True

    desired_class_names = set(argv[desired_classes_start:])
  source = utils.ReadFile(filename)
  if source is None:
    return 1

  builder = ast.BuilderFromSource(source, filename)
  try:
    entire_ast = filter(None, builder.Generate())
  except KeyboardInterrupt:
    return
  except:
    # An error message was already printed since we couldn't parse.
    sys.exit(1)
  else:
    lines = _GenerateMocks(filename, source, entire_ast, desired_class_names, mock_destructors)
    sys.stdout.write('\n'.join(lines))


if __name__ == '__main__':
  main(sys.argv)
