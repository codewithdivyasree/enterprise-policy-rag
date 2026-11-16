const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');const ts=require('typescript');
const source=fs.readFileSync(path.join(__dirname,'../src/answer-format.ts'),'utf8');const context={exports:{}};vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,context);const clean=context.exports.cleanAnswer;
test('reported citation-subject fragment retains grammatical subjects',()=>{
 assert.equal(clean('dumps returns a string [S1].\n\n[S1] provides the core distinction, and [S3] confirms that dumps returns a string.'),'dumps returns a string.\n\nThe document provides the core distinction, and the document confirms that dumps returns a string.');
});
test('factual clauses survive subject repair',()=>assert.equal(clean('[S1] states that annual leave is twelve days.'),'The document states that annual leave is twelve days.'));
test('ordinary annotations and punctuation cleaned',()=>assert.equal(clean('Values compare with == [S1], identity with is [S2].'),'Values compare with ==, identity with is.'));
test('literal source-like strings inside code remain intact',()=>{
 const text='Example [S1]:\n```python\nprint("[S1] confirms a value")\n```\nUse `[S2]` literally.';
 assert.equal(clean(text),'Example:\n```python\nprint("[S1] confirms a value")\n```\nUse `[S2]` literally.');
});
test('plural source subject has plural agreement',()=>assert.equal(clean('[S1] and [S2] confirm that x is 3.'),'The documents confirm that x is 3.'));
test('normal prose and markdown are preserved',()=>assert.equal(clean('**Return value:** `None`.\n- Keep the list.\n- Return it.'),'**Return value:** `None`.\n- Keep the list.\n- Return it.'));
test('annotation preceding a second source subject preserves conjunction',()=>assert.equal(clean('This is supported [S1], and [S2] confirms it.'),'This is supported, and the document confirms it.'));
test('cleanup is idempotent',()=>{const once=clean('[S1] explains how to use `x == y` [S2].');assert.equal(clean(once),once);});
