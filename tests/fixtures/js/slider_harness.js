// 极简假 DOM：只够跑 note_html 的滑杆 handler。argv[2] = 滑杆那段 JS。
const fs=require('fs'); const code=fs.readFileSync(process.argv[2],'utf8');
class El{constructor(tag,attrs){this.tag=tag;this.attrs=Object.assign({},attrs||{});this.children=[];this.parentNode=null;this.l={};
  const self=this;this.classList={add(c){const s=new Set((self.attrs.class||'').split(/\s+/).filter(Boolean));s.add(c);self.attrs.class=[...s].join(' ')},contains(c){return (self.attrs.class||'').split(/\s+/).includes(c)}};}
 getAttribute(k){return k in this.attrs?this.attrs[k]:null} setAttribute(k,v){this.attrs[k]=String(v)}
 appendChild(c){c.parentNode=this;this.children.push(c);return c}
 insertBefore(c,ref){c.parentNode=this;const i=ref?this.children.indexOf(ref):-1;if(i<0)this.children.push(c);else this.children.splice(i,0,c);return c}
 remove(){if(this.parentNode){const p=this.parentNode;p.children.splice(p.children.indexOf(this),1);this.parentNode=null}}
 cloneNode(){return new El(this.tag,this.attrs)}
 all(){let o=[];for(const c of this.children){o.push(c,...c.all())}return o}
 querySelectorAll(sel){const a=this.all();
  if(sel==='polyline.userline')return a.filter(e=>e.tag==='polyline'&&e.classList.contains('userline'));
  if(sel==='polyline:not(.userline)')return a.filter(e=>e.tag==='polyline'&&!e.classList.contains('userline'));
  if(sel==='polyline')return a.filter(e=>e.tag==='polyline');
  if(sel==='.slider')return a.filter(e=>e.classList.contains('slider'));
  throw new Error('sel '+sel)}
 querySelector(sel){if(sel==='input')return this.all().find(e=>e.tag==='input');if(sel==='.v')return this.all().find(e=>e.classList.contains('v'));
  const m=/^svg\[data-plot="(.*)"\]$/.exec(sel);if(m)return this.all().find(e=>e.tag==='svg'&&e.attrs['data-plot']===m[1]);throw new Error('sel '+sel)}
 addEventListener(t,f){this.l[t]=f}}
const doc=new El('doc');const svg=doc.appendChild(new El('svg',{'data-plot':'p1'}));
// series 0（有断点：两段）、series 1 是 marker（没有折线）、series 2（一段）
svg.appendChild(new El('polyline',{class:'c0',points:'a'}));svg.appendChild(new El('polyline',{class:'c0 userline',points:'b'}));
svg.appendChild(new El('polyline',{class:'c2',points:'z'}));
const opt=i=>[[`0,${i}`,'',`1,${i}`],[`m`],[`5,${i}`,'',`6,${i}`]];
const w=doc.appendChild(new El('div',{class:'slider','data-plot':'p1','data-opts':JSON.stringify([opt(0),opt(1),opt(2)]),'data-vals':'[0,1,2]'}));
const inp=w.appendChild(new El('input',{}));w.appendChild(new El('b',{class:'v'}));
global.document={querySelectorAll:s=>doc.querySelectorAll(s),querySelector:s=>doc.querySelector(s)};
const d=document; new Function('d',code)(d);
for(const v of [1,2,1]){inp.value=v;inp.l.input();}
const got=svg.querySelectorAll('polyline').map(e=>e.attrs.class+'='+e.attrs.points);
console.log(JSON.stringify(got));
