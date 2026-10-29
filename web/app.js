const $=id=>document.getElementById(id);
const methods={pc8801:[['normal-42','通常4×2'],['normal-44','通常4×4'],['normal-mix','通常Mix'],['gradient','グラデーション']],pc9801:[['cell','通常セル'],['reference','頻出Mix'],['gradient','グラデーション']]};
const presets={
  pc8801:{machine:'pc8801',method:'two_colour',paletteId:'digital8',tallDots:true,resize640:true,lineThreshold:72,frequentMode:'normal-mix',colourCoherence:12,fillStability:0},
  pc9801:{machine:'pc9801',method:'frequent',paletteId:'palette_01',tallDots:false,resize640:true,lineThreshold:58,frequentMode:'gradient',colourCoherence:10,fillStability:0.2},
};
const numericIds=['pixelSize','lineThreshold','edgeStrength','redGain','greenGain','blueGain','brightness','saturation','contrast','alphaThreshold','colourCoherence','fillStability','colourGroups'];
const ranges={pixelSize:[1,8],lineThreshold:[0,120],edgeStrength:[0,1],redGain:[0.5,1.5],greenGain:[0.5,1.5],blueGain:[0.5,1.5],brightness:[0.5,1.5],saturation:[0,2],contrast:[0.5,1.5],alphaThreshold:[0,255],colourCoherence:[1,32],fillStability:[0,1],colourGroups:[2,64]};
let table,sourceFile,sourceImage,result,worker,method='two_colour',display='result',busy=false;
let defaultFileName='digital8.png';

function status(message,error=false){$('status').textContent=message;$('status').classList.toggle('error',error);}
function options() {
  const o={machine:$('machine').value,method,paletteId:$('palette').value,tallDots:$('tallDots').checked,resize640:$('resize640').checked,
    frequentMode:$('frequentMode').value,halfTone:$('halfTone').value,quarterTone:$('quarterTone').value,
    alphaMode:$('alphaMode').value,saveMask:$('saveMask').checked,invertMask:$('invertMask').checked};
  for(const id of numericIds){const value=Number($(id).value),[lo,hi]=ranges[id];if(!Number.isFinite(value)||value<lo||value>hi)throw Error(`${$(id).closest('label')?.textContent?.trim()||id} は ${lo}～${hi} で指定してください。`);o[id]=value;}
  for(const id of ['pixelSize','lineThreshold','alphaThreshold','colourCoherence','colourGroups'])if(!Number.isInteger(o[id]))throw Error(`${id} は整数で指定してください。`);
  return o;
}
function paletteOptions(prefer) {
  const palettes=$('machine').value==='pc8801'?table.pc88_palettes:table.pc98_palettes;
  $('palette').replaceChildren(...palettes.map(p=>{const option=document.createElement('option');option.value=p.id;option.textContent=p.label;return option;}));
  $('palette').value=palettes.some(p=>p.id===prefer)?prefer:palettes[0].id;
  swatches();
}
function swatches(){const palettes=$('machine').value==='pc8801'?table.pc88_palettes:table.pc98_palettes;const palette=palettes.find(p=>p.id===$('palette').value);$('swatches').replaceChildren(...palette.colours.map(rgb=>{const item=document.createElement('span');item.style.backgroundColor=`rgb(${rgb.join(',')})`;item.title=rgb.join(', ');return item;}));}
function frequentOptions(prefer) {
  const modes=methods[$('machine').value];$('frequentMode').replaceChildren(...modes.map(([value,label])=>{const option=document.createElement('option');option.value=value;option.textContent=label;return option;}));
  $('frequentMode').value=modes.some(([value])=>value===prefer)?prefer:modes[0][0];
  $('stability-field').hidden=$('machine').value==='pc8801';
}
function setMethod(value){method=value;for(const type of ['frequent','two_colour']){$(`tab-${type}`).classList.toggle('active',type===value);$(`tab-${type}`).setAttribute('aria-selected',String(type===value));} $('frequent-options').hidden=value!=='frequent';$('two-options').hidden=value!=='two_colour';invalidate();}
function applyPreset(name){const p=presets[name];$('machine').value=p.machine;paletteOptions(p.paletteId);frequentOptions(p.frequentMode);$('tallDots').checked=p.tallDots;$('resize640').checked=p.resize640;$('lineThreshold').value=p.lineThreshold;$('colourCoherence').value=p.colourCoherence;$('fillStability').value=p.fillStability;for(const id of ['pixelSize','redGain','greenGain','blueGain','brightness','saturation','contrast'])$(id).value=1;$('edgeStrength').value=0;$('colourGroups').value=48;$('halfTone').value='checker';$('quarterTone').value='quarter_staggered';$('alphaMode').value='preserve';$('alphaThreshold').value=128;setMethod(p.method);$('preset88').classList.toggle('active',name==='pc8801');$('preset98').classList.toggle('active',name==='pc9801');status(`${name==='pc8801'?'PC-8801':'PC-9801'} 標準設定を選びました。`);}
function invalidate(){result=null;$('download').disabled=true;$('maskDownload').disabled=true;$('maskDownload').hidden=true;$('preview').disabled=!sourceImage||busy;if(sourceImage){show('source');status('設定を変更しました。変換して実寸プレビューを押してください。');}}
function show(type){display=type;$('show-result').classList.toggle('active',type==='result');$('show-source').classList.toggle('active',type==='source');$('source').hidden=type!=='source'||!sourceImage;$('output').hidden=type!=='result'||!result;$('placeholder').hidden=!!sourceImage;$('dimensions').textContent=type==='result'&&result?`${result.width} × ${result.height} px`:sourceImage?`${sourceImage.width} × ${sourceImage.height} px`:'—';if(type==='result'&&!result&&sourceImage)show('source');}
function fileName(){const palette=($('machine').value==='pc8801'?table.pc88_palettes:table.pc98_palettes).find(p=>p.id===$('palette').value);const parts=palette.label.trim().split(/\s+/);const name=parts[0].match(/^\d+$/)?parts.slice(1).join('_'):parts.join('_');const stem=(sourceFile?.name||'digital8').replace(/\.[^.]+$/,'');return `${stem}_${name}.png`;}
async function loadFile(file){if(!file)return;if(!file.type.startsWith('image/')){status('画像ファイルを選んでください。',true);return;}try{const bitmap=await createImageBitmap(file);if(bitmap.width*bitmap.height>24000000)throw Error('画像が大きすぎます。2400万画素以下にしてください。');const canvas=$('source');canvas.width=bitmap.width;canvas.height=bitmap.height;const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(bitmap,0,0);bitmap.close();sourceImage=ctx.getImageData(0,0,canvas.width,canvas.height);sourceFile=file;defaultFileName=fileName();$('file-meta').textContent=`${file.name} · ${canvas.width} × ${canvas.height} px`;$('preview').disabled=false;invalidate();show('source');status('画像を読み込みました。変換して実寸プレビューを押してください。');}catch(error){status(error.message,true);}}
function launch(){if(!sourceImage||busy)return;let o;try{o=options();}catch(error){status(error.message,true);return;}busy=true;$('preview').disabled=true;$('download').disabled=true;status('変換中… 画像によっては少し時間がかかります。');worker?.terminate();worker=new Worker(new URL('./worker.js',import.meta.url),{type:'module'});const pixels=new Uint8ClampedArray(sourceImage.data);worker.onmessage=event=>{busy=false;$('preview').disabled=false;if(!event.data.ok){status(`変換に失敗しました: ${event.data.error}`,true);return;}result=event.data.result;const canvas=$('output');canvas.width=result.width;canvas.height=result.height;canvas.getContext('2d').putImageData(new ImageData(result.data,result.width,result.height),0,0);$('download').disabled=false;$('maskDownload').hidden=!o.saveMask;$('maskDownload').disabled=!o.saveMask;show('result');status(`変換完了 · ${result.width} × ${result.height} px。保存はまだしていません。`);};worker.onerror=event=>{busy=false;$('preview').disabled=false;status(`変換に失敗しました: ${event.message}`,true);};worker.postMessage({image:{width:sourceImage.width,height:sourceImage.height,data:pixels},options:o},[pixels.buffer]);}
function saveCanvas(canvas,name){canvas.toBlob(blob=>{if(!blob){status('PNGを作成できませんでした。',true);return;}const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);status(`${name} のダウンロードを開始しました。`);},'image/png');}
function download(){if(!result)return;saveCanvas($('output'),fileName());}
function downloadMask(){if(!result)return;const canvas=document.createElement('canvas');canvas.width=result.width;canvas.height=result.height;canvas.getContext('2d').putImageData(new ImageData(result.mask,result.width,result.height),0,0);saveCanvas(canvas,fileName().replace(/\.png$/,'_alpha_mask.png'));}

async function init(){try{const response=await fetch('./converter-data.json');if(!response.ok)throw Error('パレットデータを読み込めません。');table=await response.json();applyPreset('pc8801');$('file').addEventListener('change',event=>loadFile(event.target.files[0]));$('preset88').addEventListener('click',()=>applyPreset('pc8801'));$('preset98').addEventListener('click',()=>applyPreset('pc9801'));$('machine').addEventListener('change',()=>applyPreset($('machine').value));$('palette').addEventListener('change',()=>{swatches();invalidate();});$('tab-frequent').addEventListener('click',()=>setMethod('frequent'));$('tab-two_colour').addEventListener('click',()=>setMethod('two_colour'));$('preview').addEventListener('click',launch);$('download').addEventListener('click',download);$('maskDownload').addEventListener('click',downloadMask);$('show-result').addEventListener('click',()=>show('result'));$('show-source').addEventListener('click',()=>show('source'));
    for(const input of document.querySelectorAll('input:not([type=file]),select:not(#machine):not(#palette)'))input.addEventListener('change',invalidate);
    const zone=$('dropzone');zone.addEventListener('dragover',event=>{event.preventDefault();zone.classList.add('dragging');});zone.addEventListener('dragleave',()=>zone.classList.remove('dragging'));zone.addEventListener('drop',event=>{event.preventDefault();zone.classList.remove('dragging');loadFile(event.dataTransfer.files[0]);});
  }catch(error){status(error.message,true);}}
init();
