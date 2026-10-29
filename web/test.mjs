import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {convertPixels} from './converter.js';

const table=JSON.parse(readFileSync(new URL('./converter-data.json',import.meta.url),'utf8'));
const makeImage=(w,h)=>{const data=new Uint8ClampedArray(w*h*4);for(let y=0;y<h;y++)for(let x=0;x<w;x++){const p=(y*w+x)*4;data[p]=x*255/(w-1);data[p+1]=y*255/(h-1);data[p+2]=(x+y)*127/(w+h-2);data[p+3]=x<w/2?255:110;}return {width:w,height:h,data};};
const base={machine:'pc8801',method:'two_colour',paletteId:'digital8',tallDots:true,resize640:false,pixelSize:1,lineThreshold:72,edgeStrength:0,redGain:1,greenGain:1,blueGain:1,brightness:1,saturation:1,contrast:1,alphaMode:'preserve',alphaThreshold:128,invertMask:false,halfTone:'checker',quarterTone:'quarter_staggered',colourGroups:24,frequentMode:'normal-mix',colourCoherence:12,fillStability:0};

test('four mode combinations preserve dimensions and use only their palette',()=>{
  const image=makeImage(32,17);
  for(const machine of ['pc8801','pc9801'])for(const method of ['frequent','two_colour']){
    const o={...base,machine,method,paletteId:machine==='pc8801'?'digital8':'palette_01',frequentMode:machine==='pc8801'?'normal-mix':'gradient',tallDots:machine==='pc8801'};
    const result=convertPixels(image,o,table);
    assert.equal(result.width,32);assert.equal(result.height,17);
    const palette=(machine==='pc8801'?table.pc88_palettes:table.pc98_palettes)[0].colours;
    const allowed=new Set(palette.map(rgb=>rgb.join(',')));
    for(let p=0;p<32*17;p++){
      assert.ok(allowed.has(Array.from(result.data.subarray(p*4,p*4+3)).join(',')));
      assert.equal(result.data[p*4+3],image.data[p*4+3]);
    }
    if(o.tallDots)for(let y=0;y<16;y+=2)for(let x=0;x<32;x++)assert.deepEqual(Array.from(result.data.subarray((y*32+x)*4,(y*32+x)*4+3)),Array.from(result.data.subarray(((y+1)*32+x)*4,((y+1)*32+x)*4+3)));
  }
});

test('640 resize keeps aspect ratio and binary alpha remains binary',()=>{
  const image=makeImage(80,40),o={...base,resize640:true,alphaMode:'binary',machine:'pc9801',paletteId:'palette_01',tallDots:false};
  const result=convertPixels(image,o,table);
  assert.equal(result.width,640);assert.equal(result.height,320);
  for(let p=3;p<result.data.length;p+=4)assert.ok(result.data[p]===0||result.data[p]===255);
});

test('Dinsor palette and tile variants are selectable',()=>{
  const image=makeImage(24,16),o={...base,paletteId:'dinsor',halfTone:'vertical',quarterTone:'quarter_single'};
  const result=convertPixels(image,o,table);
  assert.equal(result.width,24);assert.equal(result.height,16);
  const allowed=new Set(table.pc88_palettes[1].colours.map(rgb=>rgb.join(',')));
  for(let p=0;p<24*16;p++)assert.ok(allowed.has(Array.from(result.data.subarray(p*4,p*4+3)).join(',')));
});
