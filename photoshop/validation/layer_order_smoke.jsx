(function(){
    var root=new Folder(new File($.fileName).parent.parent.fsName+'/build/photoshop-smoke');
    var previous=app.documents.length?app.activeDocument:null,old=app.displayDialogs,doc=null;
    function png(name){var o=new PNGSaveOptions();doc.saveAs(new File(root.fsName+'/'+name+'.png'),o,true,Extension.LOWERCASE);}
    function snapshot(name){var f=new File(root.fsName+'/'+name);f.open('r');var s=f.read();f.close();return s;}
    function apply(text){var d=new ActionDescriptor();d.putString(charIDToTypeID('d8st'),text);executeAction(stringIDToTypeID('b0757cac-5771-4653-9004-388c1832665f'),d,DialogModes.NO);}
    function historyID(){var r=new ActionReference();r.putEnumerated(charIDToTypeID('HstS'),charIDToTypeID('Ordn'),charIDToTypeID('Trgt'));return executeActionGet(r).getInteger(charIDToTypeID('Idnt'));}
    function history(event,id){var r=new ActionReference(),d=new ActionDescriptor();r.putIdentifier(charIDToTypeID('HstS'),id);d.putReference(charIDToTypeID('null'),r);executeAction(charIDToTypeID(event),d,DialogModes.NO);}
    try{
        app.displayDialogs=DialogModes.NO;doc=app.open(new File(root.fsName+'/input.png'));
        doc.activeLayer.name='Original';var baseline=historyID(),count=doc.historyStates.length;png('history-baseline');
        apply(snapshot('settings-2.txt'));var generated=historyID();history('slct',baseline);png('history-restored');
        var deleteWorked=false;try{history('Dlt ',generated);deleteWorked=true;}catch(ignored){}
        var historyResult='baseline='+baseline+', restored='+historyID()+', states='+count+'->'+doc.historyStates.length+', delete='+deleteWorked;
        var d=new ActionDescriptor(),r=new ActionReference(),using=new ActionDescriptor(),adjust=new ActionDescriptor();
        r.putClass(charIDToTypeID('AdjL'));d.putReference(charIDToTypeID('null'),r);
        adjust.putInteger(charIDToTypeID('Brgh'),30);adjust.putInteger(charIDToTypeID('Cntr'),0);adjust.putBoolean(stringIDToTypeID('useLegacy'),false);
        using.putObject(charIDToTypeID('Type'),charIDToTypeID('BrgC'),adjust);d.putObject(charIDToTypeID('Usng'),charIDToTypeID('AdjL'),using);executeAction(charIDToTypeID('Mk  '),d,DialogModes.NO);
        doc.activeLayer.name='Clipped brightness +30';doc.activeLayer.grouped=true;png('adjusted-before-digital8');
        for(var i=0;i<doc.layers.length;i++){var selectLayer=new ActionDescriptor(),layerRef=new ActionReference();layerRef.putIdentifier(charIDToTypeID('Lyr '),doc.layers[i].id);selectLayer.putReference(charIDToTypeID('null'),layerRef);if(i)selectLayer.putEnumerated(stringIDToTypeID('selectionModifier'),stringIDToTypeID('selectionModifierType'),stringIDToTypeID('addToSelection'));selectLayer.putBoolean(charIDToTypeID('MkVs'),false);executeAction(charIDToTypeID('slct'),selectLayer,DialogModes.NO);}
        executeAction(stringIDToTypeID('newPlacedLayer'),undefined,DialogModes.NO);
        doc.activeLayer.name='Original + clipped adjustment';apply(snapshot('settings-2.txt'));png('adjusted-then-digital8');
        var psd=new PhotoshopSaveOptions();psd.layers=true;doc.saveAs(new File(root.fsName+'/layer-order-demo.psd'),psd,false,Extension.LOWERCASE);
        return 'Clipped adjustment inside Smart Object: applied before Digital8; '+historyResult;
    }catch(e){return 'Layer order test failed: '+e+' line='+e.line;}
    finally{if(doc){try{doc.close(SaveOptions.DONOTSAVECHANGES);}catch(ignored){}}if(previous){try{app.activeDocument=previous;}catch(ignored){}}app.displayDialogs=old;}
}());
