import unittest
import golden_gate


class RenderBoxReferenceTests(unittest.TestCase):
    def test_inlines_transitive_internal_callers_without_changing_operations(self):
        source = ('define internal float @leaf() #0 {\n'
                  '  %x = call float @custom_blend.MTL_VISIBLE_FN_REF()\n  ret float %x\n}\n'
                  'define internal float @parent() #0 {\n'
                  '  %x = call float @leaf()\n  ret float %x\n}\n'
                  'define float @entry() #0 {\n'
                  '  %x = call float @parent()\n  ret float %x\n}\n'
                  'define internal float @unrelated() #0 {\n  ret float 1.0\n}\n')
        fixed, selected = golden_gate.inline_blend_callers(source)
        self.assertEqual(selected, ['leaf', 'parent'])
        self.assertEqual(fixed.replace(' alwaysinline', ''), source)
        self.assertIn('define float @entry() #0', fixed)
        self.assertIn('define internal float @unrelated() #0', fixed)
        self.assertEqual(golden_gate.inline_blend_callers(fixed), (fixed, selected))

    def test_defers_only_blend_registration_and_keeps_call(self):
        code = ('@_ZN2RB6Shader8Constant13_shader_stateE = global i32 0\n'
                'declare float @custom_blend.MTL_VISIBLE_FN_REF()\n'
                'define float @draw() {\n'
                '  %v = call float @custom_blend.MTL_VISIBLE_FN_REF()\n'
                '  ret float %v\n}\n')
        metadata = ('!air.visible_function_references = !{!1, !2}\n'
                    '!1 = !{!"air.visible_function_reference", float ()* @custom_blend.MTL_VISIBLE_FN_REF, !"custom_blend"}\n'
                    '!2 = !{!"air.visible_function_reference", float ()* @custom_fn.MTL_VISIBLE_FN_REF, !"custom_fn"}\n')
        fixed, count = golden_gate.defer_renderbox_blend_reference(code + metadata)
        self.assertEqual(count, 1)
        self.assertTrue(fixed.startswith(code))
        self.assertIn('!air.visible_function_references = !{!2}\n', fixed)
        self.assertEqual(golden_gate.defer_renderbox_blend_reference(fixed), (fixed, 0))
        foreign = (code + metadata).replace('_ZN2RB6Shader8Constant13_shader_stateE', 'other')
        self.assertEqual(golden_gate.defer_renderbox_blend_reference(foreign), (foreign, 0))

    def test_removes_empty_registration_not_external_declaration(self):
        source = ('@_ZN2RB6Shader8Constant13_shader_stateE = global i32 0\n'
                  'declare float @custom_blend.MTL_VISIBLE_FN_REF()\n'
                  '!air.visible_function_references = !{!7}\n'
                  '!7 = !{!"air.visible_function_reference", float ()* @custom_blend.MTL_VISIBLE_FN_REF, !"custom_blend"}\n')
        fixed, count = golden_gate.defer_renderbox_blend_reference(source)
        self.assertEqual(count, 1)
        self.assertNotIn('!air.visible_function_references =', fixed)
        self.assertIn('declare float @custom_blend.MTL_VISIBLE_FN_REF()', fixed)
