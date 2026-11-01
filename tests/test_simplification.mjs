import test from 'node:test';
import assert from 'node:assert/strict';
import {bindPanels, bindMenus, importTabs} from '../app/static/lib/navigation.js';
import {revealField} from '../app/static/lib/field-folds.js';

test('secondary panels retain the same form nodes and values across switches', () => {
  const fields = {name:'同名教师 T02',note:'未提交备注'};
  const panels = ['records','roster','plans','recent'].map(id=>({dataset:{panel:id},hidden:false,fields}));
  const buttons = panels.map(p=>({dataset:{panelLink:p.dataset.panel},classList:{toggle(){}},setAttribute(){},addEventListener(k,f){this.click=f;}}));
  const root={querySelectorAll:s=>s==='[data-panel-link]'?buttons:panels};
  const select=bindPanels(root,{initial:'roster'});
  buttons[0].click();buttons[1].click();
  assert.equal(panels.filter(p=>!p.hidden)[0].dataset.panel,'roster');
  assert.equal(panels[1].fields,fields);assert.equal(fields.note,'未提交备注');
  select('unknown');assert.equal(panels[0].hidden,false);
});

test('Escape closes the active menu, restores trigger focus and removes handlers on disposal',()=>{
  let focused=false,prevented=false;
  const menu={open:true,querySelector:()=>({focus(){focused=true;}})};
  const handlers=new Map();
  const root={querySelectorAll:()=>[menu],addEventListener:(k,f)=>handlers.set(k,f),removeEventListener:k=>handlers.delete(k)};
  const dispose=bindMenus(root);
  handlers.get('keydown')({key:'Escape',target:{closest:()=>menu},preventDefault(){prevented=true;},stopPropagation(){}});
  assert.equal(menu.open,false);assert.equal(focused,true);assert.equal(prevented,true);
  dispose();assert.equal(handlers.size,0);
});

test('invalid nested optional field opens every ancestor before receiving focus',()=>{
  const outer={tagName:'DETAILS',open:false,parentElement:null};
  const inner={tagName:'DETAILS',open:false,parentElement:outer};
  const field={parentElement:inner,value:'old draft',focus(){assert.equal(outer.open,true);assert.equal(inner.open,true);},scrollIntoView(){}};
  revealField(field);assert.equal(field.value,'old draft');
});

test('import navigation retains original routes and selects the explicit destination',()=>{
  const html=importTabs('/question/new');
  for(const route of ['/ai','/question/new','/inbox'])assert.ok(html.includes(`href="#${route}"`));
  assert.equal((html.match(/aria-current="page"/g)||[]).length,1);
});
