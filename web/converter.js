// Browser port of the adopted V2/V4 conversion logic. No server-side processing.
const LUMA = [0.2126, 0.7152, 0.0722];
const BAYER = [0,8,2,10, 12,4,14,6, 3,11,1,9, 15,7,13,5];
const TEMPLATES = {
  solid: ["A"], vertical: ["AB"], checker: ["AB", "BA"],
  quarter_single: ["AB", "AA"], quarter_staggered: ["AABA", "BAAA"],
  eighth: ["AAAB", "AAAA"],
};
const clamp = (x, lo=0, hi=255) => Math.min(hi, Math.max(lo, x));
const byte = x => Math.trunc(clamp(x));
const dot = (a,b) => a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
const sq = (a,b) => (a[0]-b[0])**2+(a[1]-b[1])**2+(a[2]-b[2])**2;

export function resizeLanczos(image, targetW, targetH) {
  const {width:w,height:h,data:src}=image;
  if (targetW===w && targetH===h) return {width:w,height:h,data:new Uint8ClampedArray(src)};
  const sinc=x=>x===0?1:Math.sin(Math.PI*x)/(Math.PI*x);
  function weights(oldSize,newSize) {
    const scale=oldSize/newSize, radius=3*Math.max(1,scale), table=[];
    for(let d=0;d<newSize;d++) {
      const center=(d+0.5)*scale-0.5, entries=[]; let total=0;
      for(let s=Math.max(0,Math.ceil(center-radius));s<=Math.min(oldSize-1,Math.floor(center+radius));s++) {
        const x=(s-center)/Math.max(1,scale), weight=sinc(x)*sinc(x/3);
        if(Math.abs(x)<3) { entries.push([s,weight]); total+=weight; }
      }
      table.push(entries.map(([s,v])=>[s,v/total]));
    }
    return table;
  }
  const wx=weights(w,targetW), wy=weights(h,targetH), temp=new Float32Array(targetW*h*4);
  for(let y=0;y<h;y++) for(let x=0;x<targetW;x++) {
    const out=(y*targetW+x)*4;
    for(const [sx,weight] of wx[x]) {
      const at=(y*w+sx)*4;
      for(let c=0;c<4;c++) temp[out+c]+=src[at+c]*weight;
    }
  }
  const sums=new Float32Array(targetW*targetH*4),data=new Uint8ClampedArray(targetW*targetH*4);
  for(let y=0;y<targetH;y++) for(let x=0;x<targetW;x++) {
    const out=(y*targetW+x)*4;
    for(const [sy,weight] of wy[y]) {
      const at=(sy*targetW+x)*4;
      for(let c=0;c<4;c++) sums[out+c]+=temp[at+c]*weight;
    }
  }
  for(let i=0;i<data.length;i++)data[i]=sums[i];
  return {width:targetW,height:targetH,data};
}

function medianAndAdjust(image,o) {
  const {width:w,height:h,data:src}=image, adjusted=new Uint8Array(w*h*3), raw=new Uint8Array(w*h*3);
  const vals=new Array(9);
  for(let y=0;y<h;y++) for(let x=0;x<w;x++) {
    const p=y*w+x, base=p*4;
    for(let c=0;c<3;c++) {
      raw[p*3+c]=src[base+c];
      let median=src[base+c];
      if(x>0 && y>0 && x<w-1 && y<h-1) {
        let k=0;
        for(let dy=-1;dy<=1;dy++) for(let dx=-1;dx<=1;dx++) vals[k++]=src[((y+dy)*w+x+dx)*4+c];
        vals.sort((a,b)=>a-b); median=vals[4];
      }
      adjusted[p*3+c]=median;
    }
    const a=p*3, rgb=[adjusted[a]*o.redGain,adjusted[a+1]*o.greenGain,adjusted[a+2]*o.blueGain];
    const l=dot(rgb,LUMA);
    for(let c=0;c<3;c++) adjusted[a+c]=byte((((l+(rgb[c]-l)*o.saturation)-127.5)*o.contrast+127.5)*o.brightness);
  }
  return {raw,adjusted};
}

function inkMask(raw,w,h,o) {
  const mask=new Uint8Array(w*h), threshold=o.lineThreshold;
  for(let y=0;y<h;y++) for(let x=0;x<w;x++) {
    const p=y*w+x, a=p*3, rgb=[raw[a],raw[a+1],raw[a+2]];
    const maximum=Math.max(...rgb), minimum=Math.min(...rgb), l=dot(rgb,LUMA);
    let ink=maximum<=threshold && maximum-minimum<=Math.max(32,Math.floor(threshold/2));
    if(o.method==='two_colour') ink=ink || maximum<=threshold || (l<=threshold*0.65 && maximum<=threshold*1.5);
    if(o.edgeStrength>0 && l<170) {
      let sum=0;
      for(let dy=-1;dy<=1;dy++) for(let dx=-1;dx<=1;dx++) {
        if(dx===0 && dy===0) continue;
        const xx=clamp(x+dx,0,w-1), yy=clamp(y+dy,0,h-1), b=(yy*w+xx)*3;
        sum+=raw[b]*LUMA[0]+raw[b+1]*LUMA[1]+raw[b+2]*LUMA[2];
      }
      ink=ink || sum/8-l>=72-36*o.edgeStrength;
    }
    mask[p]=ink?1:0;
  }
  return mask;
}

function coherent(rgb,step) {
  if(step<=1) return rgb;
  const out=new Uint8Array(rgb.length), half=Math.floor(step/2);
  for(let i=0;i<rgb.length;i++) out[i]=clamp(Math.floor((rgb[i]+half)/step)*step);
  return out;
}

function familyOf(r,g,b,mode) {
  const max=Math.max(r,g,b), min=Math.min(r,g,b), chroma=max-min, sat=max?chroma/max:0;
  if(max<56) return 'dark';
  if(sat<(mode==='gradient'?0.025:0.075)) return max<224?'neutral':'light';
  let hue;
  if(r===max) hue=((g-b)/chroma+6)%6*60;
  else if(g===max) hue=((b-r)/chroma+2)*60;
  else hue=((r-g)/chroma+4)*60;
  if(hue<65 || hue>=345) return 'warm';
  if(hue<145) return 'green';
  if(hue<195) return 'teal';
  if(hue<265) return 'cool';
  return 'magenta';
}

function build88Frequent(table,palette,mode) {
  return table.pc88_v2[mode].map(r=>{
    const target=[0,0,0];
    for(const id of r.tile) for(let c=0;c<3;c++) target[c]+=palette[id][c]/r.tile.length;
    return {...r,target,yellow:r.tile.includes(6)};
  });
}

function choose88Frequent(rgb,ink,w,h,recipes,o) {
  const coherentRgb=coherent(rgb,o.colourCoherence), result=new Uint16Array(w*h), cache=new Map(), byFamily=new Map();
  recipes.forEach((r,i)=>{if(!byFamily.has(r.family)) byFamily.set(r.family,[]);byFamily.get(r.family).push(i);});
  for(let p=0;p<w*h;p++) {
    if(ink[p]) continue;
    const a=p*3, r=coherentRgb[a],g=coherentRgb[a+1],b=coherentRgb[a+2], key=(r<<16)|(g<<8)|b;
    let selected=cache.get(key);
    if(selected===undefined) {
      const family=familyOf(r,g,b,o.frequentMode), allowed=byFamily.get(family)||[];
      let best=Infinity; selected=0;
      const max=Math.max(r,g,b), min=Math.min(r,g,b), sat=max?(max-min)/max:0;
      const pale=family==='warm' && r>=g && g>=b && max>=205 && sat>=0.07 && sat<=0.35;
      for(const id of allowed) {
        const recipe=recipes[id]; let score=sq([r,g,b],recipe.target)+recipe.penalty;
        if(pale && recipe.yellow) score-=1400;
        if(score<best) {best=score;selected=id;}
      }
      cache.set(key,selected);
    }
    result[p]=selected;
  }
  return result;
}

function oklab(rgb) {
  const linear=rgb.map(v=>{v/=255;return v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4;});
  const [r,g,b]=linear;
  const l=Math.cbrt(0.4122214708*r+0.5363325363*g+0.0514459929*b);
  const m=Math.cbrt(0.2119034982*r+0.6806995451*g+0.1073969566*b);
  const s=Math.cbrt(0.0883024619*r+0.2817188376*g+0.6299787005*b);
  return [0.2104542553*l+0.793617785*m-0.0040720468*s,
    1.9779984951*l-2.428592205*m+0.4505937099*s,
    0.0259040371*l+0.782771766*m-0.808675766*s];
}

function uniqueIndices(palette) {
  const seen=new Set(), out=[];
  palette.forEach((c,i)=>{const key=c.join(',');if(!seen.has(key)){seen.add(key);out.push(i);}});
  return out;
}

function build98Frequent(palette,mode) {
  const unique=uniqueIndices(palette), recipes=[];
  for(const a of unique) recipes.push({a,b:a,coverage:0,target:oklab(palette[a])});
  for(let i=0;i<unique.length;i++) for(let j=i+1;j<unique.length;j++) {
    const a=unique[i],b=unique[j];
    for(let k=1;k<16;k++) {
      if(mode==='cell' && ![4,8,12].includes(k)) continue;
      recipes.push({a,b,coverage:k,target:oklab(palette[a].map((v,c)=>(v*(16-k)+palette[b][c]*k)/16))});
    }
  }
  return recipes;
}

function choose98Frequent(rgb,w,h,recipes,o) {
  const source=coherent(rgb,o.colourCoherence), result=new Uint16Array(w*h), cache=new Map();
  for(let p=0;p<w*h;p++) {
    const a=p*3, key=(source[a]<<16)|(source[a+1]<<8)|source[a+2];
    let chosen=cache.get(key);
    if(chosen===undefined) {
      const colour=oklab([source[a],source[a+1],source[a+2]]);
      let best=Infinity; chosen=0;
      for(let i=0;i<recipes.length;i++) {
        const score=sq(colour,recipes[i].target);
        if(score<best){best=score;chosen=i;}
      }
      cache.set(key,chosen);
    }
    result[p]=chosen;
  }
  return result;
}

function stabilise(chosen,rgb,w,h,strength) {
  if(strength<=0) return chosen;
  const limit=(4+36*strength)**2, iterations=1+Math.floor(strength*2.999);
  for(let pass=0;pass<iterations;pass++) {
    const next=new Uint16Array(chosen.length);
    for(let y=0;y<h;y++) for(let x=0;x<w;x++) {
      const p=y*w+x, ids=[], valid=[];
      for(let dy=-1;dy<=1;dy++) for(let dx=-1;dx<=1;dx++) {
        const q=clamp(y+dy,0,h-1)*w+clamp(x+dx,0,w-1), a=p*3,b=q*3;
        ids.push(chosen[q]);
        valid.push((rgb[a]-rgb[b])**2+(rgb[a+1]-rgb[b+1])**2+(rgb[a+2]-rgb[b+2])**2<=limit);
      }
      let best=-1, selected=0;
      for(let i=0;i<9;i++) {
        let votes=0;
        for(let j=0;j<9;j++) if(valid[j] && ids[j]===ids[i]) votes++;
        if(votes>best){best=votes;selected=ids[i];}
      }
      next[p]=selected;
    }
    chosen=next;
  }
  return chosen;
}

function octreeGroups(rgb,w,h,groupCount) {
  // Equivalent bucket layout to Pillow's RGB FASTOCTREE (QuantOctree.c):
  // 4/4/4-bit fine buckets plus 2/2/2-bit coarse buckets.
  const makeBucket=()=>({count:0,sum:[0,0,0]});
  const fine=Array.from({length:4096},makeBucket),coarse=Array.from({length:64},makeBucket);
  const fineKey=(r,g,b)=>((r>>4)<<8)|((g>>4)<<4)|(b>>4);
  const coarseKey=(r,g,b)=>((r>>6)<<4)|((g>>6)<<2)|(b>>6);
  for(let p=0;p<w*h;p++) {
    const a=p*3,r=rgb[a],g=rgb[a+1],b=rgb[a+2];
    const bucket=fine[fineKey(r,g,b)];bucket.count++;bucket.sum[0]+=r;bucket.sum[1]+=g;bucket.sum[2]+=b;
  }
  for(let i=0;i<4096;i++) {
    const bucket=fine[i];if(!bucket.count)continue;
    const r=i>>8,g=(i>>4)&15,b=i&15,target=coarse[((r>>2)<<4)|((g>>2)<<2)|(b>>2)];
    target.count+=bucket.count;for(let c=0;c<3;c++)target.sum[c]+=bucket.sum[c];
  }
  const fineSorted=fine.map((bucket,index)=>({bucket,index})).sort((a,b)=>b.bucket.count-a.bucket.count||a.index-b.index);
  let coarseCount=Math.min(groupCount,coarse.filter(bucket=>bucket.count>0).length);
  let fineCount=groupCount-coarseCount;
  const subtract=(start,end)=>{
    for(let i=start;i<end;i++){
      const bucket=fineSorted[i].bucket;if(!bucket.count)continue;
      const average=bucket.sum.map(sum=>Math.trunc(sum/bucket.count));
      const target=coarse[coarseKey(...average)];target.count-=bucket.count;
      for(let c=0;c<3;c++)target.sum[c]-=bucket.sum[c];
    }
  };
  subtract(0,fineCount);
  while(coarseCount>coarse.filter(bucket=>bucket.count>0).length) {
    const old=fineCount;coarseCount=coarse.filter(bucket=>bucket.count>0).length;
    fineCount=groupCount-coarseCount;subtract(old,fineCount);
  }
  const coarseSorted=coarse.map((bucket,index)=>({bucket,index})).sort((a,b)=>b.bucket.count-a.bucket.count||a.index-b.index);
  const palette=[...coarseSorted.slice(0,coarseCount),...fineSorted.slice(0,fineCount)];
  const coarseLookup=new Uint16Array(64);
  for(let i=coarseCount-1;i>=0;i--){const b=palette[i].bucket;if(b.count){const c=b.sum.map(v=>Math.trunc(v/b.count));coarseLookup[coarseKey(...c)]=i;}}
  const fineLookup=new Uint16Array(4096);
  for(let i=0;i<4096;i++)fineLookup[i]=coarseLookup[((i>>10)<<4)|((((i>>4)&15)>>2)<<2)|((i&15)>>2)];
  for(let i=groupCount-1;i>=coarseCount;i--){const b=palette[i]?.bucket;if(b?.count){const c=b.sum.map(v=>Math.trunc(v/b.count));fineLookup[fineKey(...c)]=i;}}
  const groups=new Uint16Array(w*h);
  for(let p=0;p<w*h;p++){const a=p*3;groups[p]=fineLookup[fineKey(rgb[a],rgb[a+1],rgb[a+2])];}
  return {groups,amount:groupCount};
}

function buildTwoColour(palette,o,table) {
  const unique=o.machine==='pc8801'?palette.map((_,i)=>i):uniqueIndices(palette), recipes=[];
  const observed=new Set(table.pc88_observed_pairs.map(p=>p.join(',')));
  const brightness=palette.map(c=>dot(c,LUMA));
  for(const a of unique) for(const b of unique) {
    for(const [name,rows] of Object.entries(TEMPLATES)) {
      if((a===b)!==(name==='solid'))continue;
      if(['checker','vertical'].includes(name) && brightness[a]<brightness[b])continue;
      const tile=rows.join('').split('').map(char=>char==='A'?a:b), target=[0,0,0];
      for(const index of tile)for(let c=0;c<3;c++)target[c]+=palette[index][c]/tile.length;
      let penalty=a===b?0:sq(palette[a],palette[b])*0.002;
      if(name==='checker' || name==='vertical') if(name!==o.halfTone)penalty+=500;
      if(name==='quarter_staggered' || name==='quarter_single') if(name!==o.quarterTone)penalty+=500;
      if(o.machine==='pc8801' && a!==b) {
        if(!observed.has([Math.min(a,b),Math.max(a,b)].join(',')))penalty+=240;
        if(a!==0 && a!==7 && b!==0 && b!==7 && (a^b)===7)penalty+=1800;
      }
      recipes.push({tile,height:rows.length,width:rows[0].length,target,penalty});
    }
  }
  return recipes;
}

function chooseTwoColour(rgb,ink,w,h,recipes,o) {
  const {groups,amount}=octreeGroups(rgb,w,h,o.colourGroups), sums=Array.from({length:amount},()=>[0,0,0]),counts=new Uint32Array(amount);
  for(let p=0;p<w*h;p++)if(!ink[p]){const id=groups[p],a=p*3;for(let c=0;c<3;c++)sums[id][c]+=rgb[a+c];counts[id]++;}
  const bestIds=new Uint16Array(amount);
  const targetChroma=recipes.map(r=>{const mean=(r.target[0]+r.target[1]+r.target[2])/3,v=r.target.map(c=>c-mean);return {v,len:Math.hypot(...v)};});
  for(let id=0;id<amount;id++) {
    if(!counts[id])continue;
    const colour=sums[id].map(v=>v/counts[id]), mean=colour.reduce((a,b)=>a+b,0)/3;
    const chroma=colour.map(c=>c-mean),length=Math.hypot(...chroma);
    let primary=-1;
    for(let c=0;c<3;c++)if([0,1,2].every(other=>other===c || colour[c]-colour[other]>=35)){primary=c;break;}
    let best=Infinity,selected=0;
    for(let i=0;i<recipes.length;i++) {
      const r=recipes[i];let score=sq(r.target,colour)+r.penalty;
      if(length>12){const tc=targetChroma[i];const cosine=dot(tc.v,chroma)/Math.max(tc.len*length,1);score+=6000*Math.min(1,length/80)*(1-cosine);}
      if(primary>=0 && [0,1,2].some(other=>other!==primary && r.target[primary]<=r.target[other]+5))score+=9000;
      if(score<best){best=score;selected=i;}
    }
    bestIds[id]=selected;
  }
  const selected=new Uint16Array(w*h);
  for(let p=0;p<w*h;p++)selected[p]=bestIds[groups[p]];
  return selected;
}

function renderTiles(selected,ink,w,h,recipes,darkest) {
  const out=new Uint8Array(w*h);
  for(let y=0;y<h;y++)for(let x=0;x<w;x++) {
    const p=y*w+x,r=recipes[selected[p]];
    out[p]=ink[p]?darkest:r.tile[(y%r.height)*r.width+x%r.width];
  }
  return out;
}

function render98Frequent(selected,ink,w,h,recipes,palette,mode,table) {
  const out=new Uint8Array(w*h),darkest=darkestIndex(palette),masks=table.pc98_masks;
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){
    const p=y*w+x,r=recipes[selected[p]],coverage=r.coverage;
    const bit=mode==='gradient'?BAYER[(y%4)*4+x%4]<coverage
      :(masks[String(coverage)]||BAYER.map(v=>v<coverage))[(y%4)*4+x%4];
    out[p]=ink[p]?darkest:(bit?r.b:r.a);
  }
  return out;
}

function darkestIndex(palette) {
  let chosen=0,best=Infinity;
  palette.forEach((colour,i)=>{const l=dot(colour,LUMA);if(l<best){best=l;chosen=i;}});
  return chosen;
}

export function convertPixels(input,options,table) {
  const o=options, palettes=o.machine==='pc8801'?table.pc88_palettes:table.pc98_palettes;
  const palette=palettes.find(p=>p.id===o.paletteId)?.colours;
  if(!palette)throw Error('パレットが見つかりません');
  let original=input;
  if(o.resize640 && input.width!==640)original=resizeLanczos(input,640,Math.max(1,Math.round(input.height*640/input.width)));
  const outputW=original.width,outputH=original.height;
  let logical=o.tallDots?resizeLanczos(original,outputW,Math.ceil(outputH/2)):original;
  const logicalW=logical.width,logicalH=logical.height;
  if(o.pixelSize>1)logical=resizeLanczos(logical,Math.max(1,Math.floor(logicalW/o.pixelSize)),Math.max(1,Math.floor(logicalH/o.pixelSize)));
  const w=logical.width,h=logical.height,{raw,adjusted}=medianAndAdjust(logical,o),ink=inkMask(raw,w,h,o);
  let selected,recipes,indices;
  if(o.method==='two_colour') {
    recipes=buildTwoColour(palette,o,table);
    selected=chooseTwoColour(adjusted,ink,w,h,recipes,o);
    indices=renderTiles(selected,ink,w,h,recipes,darkestIndex(palette));
  } else if(o.machine==='pc8801') {
    recipes=build88Frequent(table,palette,o.frequentMode);
    selected=choose88Frequent(adjusted,ink,w,h,recipes,o);
    indices=renderTiles(selected,ink,w,h,recipes,darkestIndex(palette));
  } else {
    recipes=build98Frequent(palette,o.frequentMode);
    selected=choose98Frequent(adjusted,w,h,recipes,o);
    selected=stabilise(selected,adjusted,w,h,o.fillStability);
    indices=render98Frequent(selected,ink,w,h,recipes,palette,o.frequentMode,table);
  }
  const out=new Uint8ClampedArray(outputW*outputH*4), mask=new Uint8ClampedArray(outputW*outputH*4);
  for(let y=0;y<outputH;y++)for(let x=0;x<outputW;x++) {
    const yy=o.tallDots?Math.floor(y/2):y;
    const gx=o.pixelSize>1?Math.min(w-1,Math.floor(x*w/logicalW)):x;
    const gy=o.pixelSize>1?Math.min(h-1,Math.floor(yy*h/logicalH)):Math.min(h-1,yy);
    const colour=palette[indices[gy*w+gx]],p=(y*outputW+x)*4,alpha=original.data[p+3];
    for(let c=0;c<3;c++)out[p+c]=colour[c];
    const a=o.alphaMode==='binary'?(alpha>=o.alphaThreshold?255:0):alpha;
    out[p+3]=a;
    for(let c=0;c<3;c++)mask[p+c]=o.invertMask?255-a:a;
    mask[p+3]=255;
  }
  return {width:outputW,height:outputH,data:out,mask};
}
