import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
const source=await fs.readFile(new URL('../promo-site/assets/app.js',import.meta.url),'utf8');
const context=vm.createContext({});
vm.runInContext(`const stringOr=v=>String(v??''); const extractUrl=v=>String(v??''); const isImportedVideo=(a,b)=>/视频/.test(a+b);`+
 source.slice(source.indexOf('function csvRowsToRecords'),source.indexOf('function isImportedVideo')),context);
const run=code=>vm.runInContext(code,context);
test('CSV matches latest decorated headers and keeps zero values regardless of column order',()=>{
 const result=run(`csvRowsToRecords([['缩略图','专页名字','👍点赞','帖文链接','转换率','管理员&#x20;','交教会'],['https://example.com/a.jpg','示例专页',0,'https://example.com/post',0.25,'示例管理员',4]])[0]`);
 assert.equal(result.name,'示例管理员');assert.equal(result.pageName,'示例专页');assert.equal(result.likes,0);
 assert.equal(result.conversionRate,0.25);assert.equal(result.water,4);
 assert.equal(result.thumbnailUrl,'https://example.com/a.jpg');
});
test('CSV has no positional or legacy letter fallback',()=>{
 assert.throws(()=>run(`csvRowsToRecords([['示例','https://example.com/post']])`),/表头/);
 assert.throws(()=>run(`csvRowsToRecords([['帖文链接','帖文链接'],['a','b']])`),/重复/);
 const result=run(`csvRowsToRecords([['帖文链接','未知列'],['https://example.com/post','不会当作帖文ID']])[0]`);
 assert.equal(result.postId,undefined);assert.equal(result.thumbnailUrl,'');
});
