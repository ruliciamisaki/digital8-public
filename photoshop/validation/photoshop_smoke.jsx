// Uses Photoshop's scripting API on disposable documents only. Never saves,
// filters or closes any document that was already open before this script.
(function () {
    var root = new Folder(new File($.fileName).parent.parent.fsName + "/build/photoshop-smoke");
    var oldDialogs = app.displayDialogs;
    var previous = app.documents.length ? app.activeDocument : null;
    var owned = [], records = [], initialCount = app.documents.length;
    function quote(value) { return '"' + String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r/g, '\\r').replace(/\n/g, '\\n').replace(/\t/g, '\\t') + '"'; }
    function json(value) {
        if (value === null) return 'null';
        if (typeof value === 'string') return quote(value);
        if (typeof value === 'number' || typeof value === 'boolean') return String(value);
        var result = [], i;
        if (value instanceof Array) { for (i=0;i<value.length;i++) result.push(json(value[i])); return '['+result.join(',')+']'; }
        for (var key in value) if (value.hasOwnProperty(key)) result.push(quote(key)+':'+json(value[key]));
        return '{'+result.join(',')+'}';
    }
    function report() { var file = new File(root.fsName+'/results.json'); file.encoding='UTF8'; file.open('w'); file.write(json(records)); file.close(); }
    function read(name) { var f=new File(root.fsName+'/'+name); f.encoding='UTF8'; if(!f.open('r')) throw new Error('Cannot open '+name); var s=f.read(); f.close(); return s; }
    function openInput() { var doc=app.open(new File(root.fsName+'/input.png')); owned.push(doc); return doc; }
    function png(doc,name) { var options=new PNGSaveOptions(); options.interlaced=false; doc.saveAs(new File(root.fsName+'/'+name+'.png'),options,true,Extension.LOWERCASE); }
    function apply(snapshot) { var descriptor=new ActionDescriptor(); descriptor.putString(charIDToTypeID('d8st'),snapshot); executeAction(stringIDToTypeID('b0757cac-5771-4653-9004-388c1832665f'),descriptor,DialogModes.NO); }
    function findSnapshot(descriptor,snapshot,depth) {
        if(depth>12) return false;
        for(var i=0;i<descriptor.count;i++) {
            var key=descriptor.getKey(i),type=descriptor.getType(key);
            if(type===DescValueType.STRINGTYPE && descriptor.getString(key)===snapshot) return true;
            if(type===DescValueType.OBJECTTYPE && findSnapshot(descriptor.getObjectValue(key),snapshot,depth+1)) return true;
            if(type===DescValueType.LISTTYPE) {
                var list=descriptor.getList(key);
                for(var j=0;j<list.count;j++) {
                    if(list.getType(j)===DescValueType.STRINGTYPE && list.getString(j)===snapshot) return true;
                    if(list.getType(j)===DescValueType.OBJECTTYPE && findSnapshot(list.getObjectValue(j),snapshot,depth+1)) return true;
                }
            }
        }
        return false;
    }
    function layerDescriptor() { var ref=new ActionReference(); ref.putEnumerated(charIDToTypeID('Lyr '),charIDToTypeID('Ordn'),charIDToTypeID('Trgt')); return executeActionGet(ref); }
    try {
        app.displayDialogs=DialogModes.NO;
        records.push({stage:'start',photoshop:app.version,existing_documents:app.documents.length}); report();
        for(var machine=0;machine<3;machine++) {
            var doc=openInput(); png(doc,'before-'+machine);
            apply(read('settings-'+machine+'.txt'));
            png(doc,'normal-'+machine);
            records.push({stage:'normal',machine:machine,ok:true}); report();
            doc.close(SaveOptions.DONOTSAVECHANGES);
        }
        var smart=openInput(),snapshot=read('settings-2.txt');
        executeAction(stringIDToTypeID('newPlacedLayer'),undefined,DialogModes.NO);
        apply(snapshot); png(smart,'smart-before');
        var desc=layerDescriptor(),found=findSnapshot(desc,snapshot,0);
        records.push({stage:'smart-apply',snapshot_found:found,layer_kind:String(smart.activeLayer.kind)}); report();
        if(!found) throw new Error('Settings snapshot not found in smart-filter layer descriptor');
        var psdOptions=new PhotoshopSaveOptions(); psdOptions.layers=true; psdOptions.embedColorProfile=true;
        smart.saveAs(new File(root.fsName+'/smart.psd'),psdOptions,false,Extension.LOWERCASE);
        smart.close(SaveOptions.DONOTSAVECHANGES);
        smart=app.open(new File(root.fsName+'/smart.psd')); owned.push(smart);
        var restored=findSnapshot(layerDescriptor(),snapshot,0); png(smart,'smart-reopened');
        records.push({stage:'smart-reopen',snapshot_found:restored}); report();
        if(!restored) throw new Error('Smart-filter snapshot did not survive PSD reopening');
        executeAction(stringIDToTypeID('placedLayerEditContents'),undefined,DialogModes.NO);
        var contents=app.activeDocument; owned.push(contents);
        contents.activeLayer.adjustBrightnessContrast(20,0); png(contents,'changed-source');
        contents.save(); contents.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument=smart; png(smart,'smart-recomputed');
        records.push({stage:'smart-source-change',snapshot_found:findSnapshot(layerDescriptor(),snapshot,0),ok:true}); report();
        smart.close(SaveOptions.DONOTSAVECHANGES);
        records.push({stage:'complete',ok:true}); report();
        return 'Digital8 Photoshop smoke tests completed';
    } catch(error) {
        records.push({stage:'error',message:String(error),line:error.line}); report();
        return 'Digital8 Photoshop smoke test failed: '+String(error);
    } finally {
        for(var i=owned.length-1;i>=0;i--) { try { owned[i].close(SaveOptions.DONOTSAVECHANGES); } catch(ignored) {} }
        try { if(previous) app.activeDocument=previous; } catch(ignoredRestore) {}
        app.displayDialogs=oldDialogs;
        records.push({stage:'cleanup',initial_documents:initialCount,remaining_documents:app.documents.length}); report();
    }
}());
