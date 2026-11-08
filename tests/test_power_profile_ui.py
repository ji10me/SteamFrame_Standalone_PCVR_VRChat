"""The visible system switch must follow a requested VR power profile."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from measure_frame_thermals import set_profile, system_profile


class PowerProfileUI(unittest.TestCase):
    def run_request(self, initial):
        captured = []
        set_profile(42, 'Powersave', lambda js: captured.append(js) or {'profile_request': 'Powersave'})
        source = r'''
const assert = require('node:assert/strict');
const state = {client: INITIAL, vr:'Performance', recording:false, fields:[]};
const settings = {rV:{clientSettings:{}}, qt: async (key,value)=>{
  assert.equal(key,'steamos_platform_performance_profile'); state.client=value;
}};
Object.defineProperty(settings.rV.clientSettings,'steamos_platform_performance_profile',{
  get:()=>state.client
});
global.window={__framePerfRequire: id=>{
  if(id===39828) return settings;
  if(id===42) return {p:{IsConnected:()=>true,SteamVR:{SetHMDSettings:async delta=>{
    state.fields=Object.keys(delta); state.vr=delta.performance_profile_value;
  }}}};
  throw Error('unexpected module');
}};
(async()=>{
  try { await eval(EXPRESSION); }
  catch(error) {state.error=error.message;}
  console.log(JSON.stringify(state));
})();
'''.replace('INITIAL', json.dumps(initial)).replace('EXPRESSION', json.dumps(captured[0]))
        node = shutil.which('node')
        self.assertIsNotNone(node)
        result = subprocess.run([node, '-e', source], check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def test_request_updates_visible_switch_and_vr_without_changing_recording(self):
        result = self.run_request('Performance')
        self.assertEqual(result['client'], 'Powersave')
        self.assertEqual(result['vr'], 'Powersave')
        self.assertFalse(result['recording'])
        self.assertEqual(result['fields'], ['performance_profile_value'])
        self.assertNotIn('error', result)

    def test_external_profile_is_preserved_before_any_write(self):
        result = self.run_request('Balanced')
        self.assertEqual(result['client'], 'Balanced')
        self.assertEqual(result['vr'], 'Performance')
        self.assertEqual(result['fields'], [])
        self.assertIn('External system profile', result['error'])

    def test_visible_switch_reader_rejects_missing_or_external_values(self):
        self.assertEqual(system_profile(lambda _: 'Powersave'), 'Powersave')
        for value in (None, '', 'Balanced', True):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                system_profile(lambda _: value)


if __name__ == '__main__':
    unittest.main()
