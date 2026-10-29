import {convertPixels} from './converter.js';

const tablePromise=fetch(new URL('./converter-data.json',import.meta.url)).then(response=>{
  if(!response.ok)throw Error('変換データを読み込めません');
  return response.json();
});

self.onmessage=async event=>{
  try {
    const {image,options}=event.data;
    const table=await tablePromise;
    const result=convertPixels(image,options,table);
    self.postMessage({ok:true,result},[result.data.buffer,result.mask.buffer]);
  } catch(error) {
    self.postMessage({ok:false,error:error?.message||String(error)});
  }
};
