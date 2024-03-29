var map = null;

var volcanoMarkers=[]
let volcanoTooltips=[]
let customLabelLocs={}

let stationMarkers=[]
let stationTooltips=[]

let uncheckedMarkers=[]

var overviewRatio = 5;
var staTimer = null;
var monitorSocket = null;
var pingTimer = null;
var units = "i"

const BASE_FONT_SIZE=20;
const BASE_MARKER_SIZE=20;
const BASE_MAP_SIZE=[1232,924];
let scale=1;

const labelOffsets={
    BL: [[17,-33],'left'], //top right
    BR: [[-17,-33],'right'], //top left
    TR: [[-17,17],'right'],  //bottom left
    TL: [[17,17],'left'],  //bottom right
    BC: [[0,-33],'center'], //top center
    ML: [[17,-6],'left'],   //right
    TC: [[0,17],'center'],  //bottom center
    MR: [[-17,-6],'right']    //left
}

const volcColors={
    volcanoRED:'#EC0000',
    volcanoGREEN:'#87C264',
    volcanoYELLOW:'#FFFF66',
    volcanoORANGE:'#FF9933',
    volcanoUNASSIGNED:'#777777'
}

/*
multiplication factor to go from one unit of measure to another
Key is two characters: source unit and destination unit.
p=pixels
i=inches
c=centimeters
Unit characters come from GMT
*/
var conversions = {
    'pi': (1 / 300), //pixels -->inches, 300 DPI
    'ip': 300, //inches --> pixels, 300 DPI
    'ic': 2.54, //inches --> cm
    'ci': 1 / 2.54, //cm --> inches
    'cp': 300 / 2.54, //cm  --> pixels
    'pc': 2.54 / 300 //pixels --> cm
}

var staCategories = {
    999: 'User Defined',
    1: 'Seismometer',
    101740:'Seismometer',
    3: 'Tiltmeter',
    101742:'Tiltmeter',
    4: 'GPS',
    101743:'GPS',
    7: 'Gas',
    101746: 'Gas',
    12: 'Temperature',
   // 101751:'Temperature',
    22: 'Camera',
    101761: 'Camera',
    23: 'Infrasound',
    130195:'Infrasound'
}

window.onbeforeunload = function() {
    //make sure the downloading overlay is hidden whenever we navigate away from the page.
    $('#downloading').hide();
}

function setCookie(name, value, expiresInSeconds) {
    var exdate = new Date();
    exdate.setTime(exdate.getTime() + expiresInSeconds * 1000);
    var c_value = escape(value) + ((expiresInSeconds == null) ? "" : "; expires=" + exdate.toUTCString());
    document.cookie = name + "=" + c_value + '; path=/';
}

function getCookie(name) {
    var parts = document.cookie.split(name + "=");
    if (parts.length == 2) return parts.pop().split(";").shift();
}

function expireCookie(name) {
    document.cookie = encodeURIComponent(name) + "=; path=/; expires=" + new Date(0).toUTCString();
}

$(document).ready(function() {
    $(document).on('change', '.mapSize', sizeMap);
    $(document).on('click', 'input.staCheck', checkForAll);
    $(document).on('click', 'input.staCatAll', toggleAll);
    $('.sectionSelectAll').click(function(e){
        e.stopPropagation();
        toggleStations.call(this,true);
    });
    $(document).on('click', 'button.deleteInset', removeInsetMap);
    $(document).on('change', '#overview, #overviewWidth', setOverviewDiv);
    $(document).on('change', '.staCheck',plotMarkers);
    $(document).on('change', '.staCheck', trackUnchecked);
    $(document).on('change','#volcLabelsTable tbody tr td input',updateVolcOffset)

    $('#overviewWidth').change(function() { overviewChanged = true; })
    $('#getMap').click(getMap);

    initMap();

    $(window).resize(sizeMap);
    $('#showVolcColor').change(plotMarkers);
    $('#editVolcLocs').click(showVolcLabelEditor);
    $('.labelLocation').change(labelLocationChanged);
    $('.latLon').change(setBounds);
    $('.reload').click(updateBounds);
    $('#addNewMap').click(addNewMap);
    $('#resetOverview').click(resetOverview);
    $('#mapLocation').change(locSelectChanged);
    $('#overviewWidth').change(overviewWidthChanged);
    $('#addStationCSV').change(addCSVStations);
    $('#overviewUnits').text($('#sizeUnits option:selected').text());
    $('#sizeUnits').change(changeUnits);
    $('#overlayFormat').change(changeFileType);
    $('#plotDataCSV').change(parseDataHeaders);
    $('.setCM').click(openCMSelector);
    $('area.cmArea').click(selectColormap);
    $('#clearStationCSV').click(clearCSV);
    
    $('#closeVolcLabel').click(function(){
      $('#volcLabelPosShield').hide();  
    })

    $('#cmCancel').click(function(){
        $('#cmSelector').hide();
    });
    $('#dataTrans').change(function(){
        $('#transLevel').text($(this).val());
    })

    $('div.help').hover(showHelp,hideHelp);
    $('button.tab').click(showTab);

    $('#legendOptions').click(function(){
        $('#legendOptionsDlg').show();
    })

    $('#closeLegendOptions').click(function(){
        $('#legendOptionsDlg').hide();
    })

    $('#lbkt')[0].oninput=function(){
        $('#lbktTransValue').text(this.value);
    }

    $('#stationOptions').click(function(){
        const target=$('#stationDisplayOpts').show();
        const halfWidth=target.width()/2;
        target.css('left',`calc( 50% - ${halfWidth}px )`);
    })

    $('#closeStaOpts').click(function(){
        $('#stationDisplayOpts').hide();
        refreshStationMarkerOpts();
        plotMarkers();
    })

    $('#optSelectorShield').click(function(){
        $('#staIconOpts').hide();
        $(this).hide();
    })

    $('.staIconOpt').click(setSymbol);

    $('.staOptDropdown').click(showIconOptions)

    $('#volcLabelPosDiv').draggable({
        handle:"div.topDiv, dif.topDiv h2"
    })

    changeFileType();
    setOverviewDiv();
    getStationsDebounce();
    setupAccordion();
    refreshStationMarkerOpts();
});

function clearCSV(){
    csvStations=[];
    $('#addStationCSV').val(null);
    displayStations();
    $('#clearStationCSV').hide();
}

function updateVolcOffset(){
    //this should be an offset input box
    const row=$(this).closest('tr.volcOffsetRow');
    const checkID=row.data('volc');
    const currVal=JSON.parse($(`#${checkID}`).val())
    let xoffset=row.find('td.xoffset input').val();
    let yoffset=row.find('td.yoffset input').val();
    // Re-invert the yoffset to leaflet coordinate system (up is down)
    yoffset*=-1;

    let defOffset,dir;
    [defOffset,dir]=labelOffsets[$('#volcLabelLocation').val()];
    currVal['offx']=xoffset-defOffset[0];
    currVal['offy']=yoffset-defOffset[1];
    $(`#${checkID}`).val(JSON.stringify(currVal));

    plotMarkersRun();
}

function makeDraggable(popup,checkID,offset){
    const pos = map.latLngToLayerPoint(popup.getLatLng());
    L.DomUtil.setPosition(popup._wrapper.parentNode, pos);
    var draggable = new L.Draggable(popup._container, popup._wrapper);
    draggable.enable();
    
    draggable.on('dragend', function() {
        const volcCheck=$('#'+checkID);
        const checkVal=JSON.parse(volcCheck.val());
        var newLatLon = map.layerPointToLatLng(this._newPos);
        let dx=this._newPos['x']-pos['x'];
        let dy=this._newPos['y']-pos['y'];
        popup.setLatLng(newLatLon);

        checkVal['labelLat']=newLatLon['lat'];
        checkVal['labelLon']=newLatLon['lng'];

        // new position is change in position PLUS the default offset, 
        // but we only want to save the difference between the default 
        // offset position and the current position.
        const offx=dx-offset[0];
        const offy=dy-offset[1];
        customLabelLocs[checkVal['name']]=[offx,offy];

        checkVal['offx']=offx/scale;
        checkVal['offy']=offy/scale;


        volcCheck.val(JSON.stringify(checkVal));

        // again have to clear the top/bottom CSS leaflet adds to get 
        // the label to show up in the correct position.
        $(popup._container).css('top','').css('bottom','');
    });
}

function makeOffsetEntry(val){
    const picker=$('<input type=number max=500 min=-500 step=1>');
    picker.val(val);
    const item=$('<td>');
    item.append(picker);
    return item;
}

function showVolcLabelEditor(){
    if($('#volcLabelLocation').val()==''){
        return;
    }

    const listTable=$('#volcLabelsTable tbody').empty();
    const checkedVolcs=getChecked('div.volc');
    let offset,dir;
    [offset,dir]=labelOffsets[$('#volcLabelLocation').val()];
    $(checkedVolcs).each(function(idx,volc){
        const checkID=computeItemID(volc['name'],'volc');

        const row=$(`<tr class="volcOffsetRow" data-volc=${checkID}>`);
        const curData=JSON.parse($('#'+checkID).val());
        let offx=offset[0]
        let offy=offset[1]
        if(curData['offx'] || curData['offy']){
            offx=curData['offx']+offset[0];
            offy=curData['offy']+offset[1];
        }

        //Invert the Y offset so up is up and down is down
        offy*=-1;

        row.append(`<td>${volc['name']}`);
        const xentry=makeOffsetEntry(offx).addClass('xoffset');
        const yentry=makeOffsetEntry(offy).addClass('yoffset');
        row.append(xentry);
        row.append(yentry);

        listTable.append(row);
    })

    $('#volcLabelPosShield').show();
}

function setSymbol(){
    const target=$('#staIconOpts').data('target');
    const symbol=$(this).data('symbol');
    const url=$(this).data('url');

    if(url=='other'){
        target.addClass('custom');
    }
    else{
        target.find('input').val(symbol);
        target.find('img').prop('src',url);
        target.removeClass('custom');
    }

    const colorSelector=target.closest('td').siblings('td.color').find('input');
    if(symbol.endsWith('.eps')){
        colorSelector.addClass('hidden');
    }
    else{
        colorSelector.removeClass('hidden');
    }

    $('#staIconOpts').hide();
    $('#optSelectorShield').hide();
    setTimeout(positionOpts,50);
}

function positionOpts(){
    const target=$('#stationDisplayOpts');
    const halfWidth=target.width()/2;
    target.css('left',`calc( 50% - ${halfWidth}px )`);
}

function showIconOptions(){
    const windowBottom= $(window).scrollTop() + $(window).height()-20;
    const totalWidth=$(this).parent().width();
    const targetWidth=totalWidth*.75;
    const thisLeft=$(this).offset().left;
    const thisBottom=$(this).offset().top+$(this).height();
    const thisWidth=$(this).width();
    const targetLeft=thisLeft+thisWidth-targetWidth-10;
    const parentDiv=$(this).closest('.staIconSelector');

    $('#staIconOpts')
        .css('min-width',targetWidth)
        .css('left',targetLeft)
        .css('top',thisBottom)
        .css('height','')
        .data('target',parentDiv)
        .show();

    let iconOptsHeight=$('#staIconOpts').height();
    const iconOptsBottom=$('#staIconOpts').offset().top+iconOptsHeight;
    if (iconOptsBottom>windowBottom){
        iconOptsHeight-=(iconOptsBottom-windowBottom);
        $('#staIconOpts').css('height',iconOptsHeight);
    }

    $('#optSelectorShield').show();
}

function showTab(){
    const target=$(this).data('target');
    $(this).siblings().removeClass('current');
    $(this).parent().parent().find('div.tabContent').hide();
    $(`#${target}`).show();
    $(this).addClass('current');
}

function setupAccordion(){
    // fix the width of the settings bar so it doesn't change as we open/close segments
    const width=$('#setupInner').width();
    $('#setupInner').css('width',width);

    //close all but the first section
    $('#setupInner div.setupContent:first').siblings('div.setupContent').hide();
    $('#setupInner div.setupHeader:first').addClass('accordion-open')

    //function to actually implement accordion behavior
    $('.setupHeader').click(function(){
        const header=$(this);
        const content=header.next();
        const isOpen=header.hasClass('accordion-open');

        $('#setupInner div.setupContent').slideUp();
        $('div.accordion-open').removeClass('accordion-open');

        if(!isOpen){
            content.slideDown()
            header.addClass('accordion-open');
        }
    })
}

function showHelp(){
    //help text has to be fixed position
    const helpText=$(this).find('div.helpText');
    helpText.show();
    const rect=helpText[0].getBoundingClientRect();
    console.log(rect);

    const winBottom=window.innerHeight || document.documentElement.clientHeight;
    if(rect.bottom>winBottom){
        helpText.css('bottom','5px');
    }
}

function hideHelp(){
    const helpText=$(this).find('div.helpText').hide();
    helpText.css('bottom','');
}

function initMap() {
    //size the map div
    sizeMap();

    const tiles = L.tileLayer('https://basemap.nationalmap.gov/ArcGIS/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}')

    map = L.map('topMap', {
        zoomSnap: 0,
        tap: false,
        layers: [tiles],
        renderer: L.canvas()
    });

    L.latlngGraticule({
        showLabel: true,
        zoomInterval: [
            { start: 2, end: 3, interval: 30 },
            { start: 3, end: 4, interval: 10 },
            { start: 4, end: 8, interval: 2 },
            { start: 8, end: 10, interval: .5 },
            { start: 10, end: 15, interval: .25 }
        ]
    }).addTo(map);

    map.on('load', function() {
        updateBounds();
        setTimeout(sizeMap, 10);
    });

    map.setView([58, -164], 5);

    map.on("moveend", updateBounds);
    map.on("zoomend", updateBounds);
    map.on("moveend zoomend", getStationsDebounce);
}

function openCMSelector(){
    const target=$(this).data('target');
    $('#cmSelector').data('target',target).css('display','grid');
}

function selectColormap(){
    const cm=$(this).data('cm');
    const target=$(`#${$('#cmSelector').data('target')}`);
    target.val(cm);
    $('#cmSelector').hide();
}

function changeUnits() {
    $('#overviewUnits').text($('#sizeUnits option:selected').text());
    var new_units = $(this).val();
    var conversion = conversions[units + new_units];
    units = new_units;
    var width = $('#mapWidth').val();
    var height = $('#mapHeight').val();
    var overview_width = $('#overviewWidth').val();

    width = width * conversion;
    height = height * conversion;

    if ($('#lockWidth').is(':checked')) {
        overview_width = width / overviewRatio;
    } else {
        overview_width = overview_width * conversion;
    }

    if (units == 'p') {
        //round to integer for pixels
        width = Math.round(width);
        height = Math.round(height);
        overview_width = Math.round(overview_width);
    } else {
        //round to two decimals
        width = Math.round(width * 100) / 100;
        height = Math.round(height * 100) / 100;
        overview_width = Math.round(overview_width * 100) / 100;
    }

    $('#overviewWidth').val(overview_width);
    $('#mapWidth').val(width);
    $('#mapHeight').val(height);

    setTimeout(setOverviewDiv, 250);
}

var overviewMap = null;

function overviewWidthChanged() {
    overviewRatio = $('#mapWidth').val() / $(this).val()
}

function resetOverview() {
    if (overviewMap === null) {
        setOverviewDiv();
    }

    ak_bounds = [
        [48.5, -190.0],
        [69.5, -147.68]
    ]

    overviewMap.fitBounds(ak_bounds);
}

function setOverviewDiv() {
    var pos = $('#overview').val();

    if (pos === 'False') {
        $('#overviewMap').hide();
    } else {
        $('#overviewMap').show();
    }

    $('#overviewMap').css('inset', '');
    var offset = "10px";

    switch (pos) {
        case "BR":
            $('#overviewMap').css('bottom', offset).css('right', offset);
            break;
        case "BL":
            $('#overviewMap').css('bottom', offset).css('left', offset);
            break;
        case "TR":
            $('#overviewMap').css('top', offset).css('right', offset);
            break;
        case "TL":
            $('#overviewMap').css('top', offset).css('left', offset);
            break;
    }

    var mapWidth = $('#topMap').width();
    var desiredWidth = Number($('#mapWidth').val());
    var ratio = mapWidth / desiredWidth;
    var disp_size = ratio * Number($('#overviewWidth').val());
    $('#overviewMap')
        .css('width', disp_size + "px")
        .css('height', disp_size + "px");

    if (overviewMap === null) {
        var tiles = L.tileLayer('https://basemap.nationalmap.gov/ArcGIS/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}')
        overviewMap = L.map('overviewMap', {
            tap: false,
            zoomSnap: 0,
            layers: [tiles]
        });

        overviewMap.on('moveend zoomend', function() {
            var bounds = overviewMap.getBounds().toBBoxString();
            $('#overviewBounds').val(bounds);
        })

        setTimeout(resetOverview, 100);
    }

    overviewMap.invalidateSize(true);
}


function changeFileType() {
    var type = $('#overlayFormat').val();
    var fileDiv = $('#overlayFiles').empty();
    if (type == 't') {
        fileDiv.append("Image (.tiff):<br>");
        fileDiv.append("<input type='file' id='imgFile'  name='imgFile'>")
    } else if (type == 'j') {
        fileDiv.append('Image (.jpg/.tif):<br>')
        fileDiv.append("<input type='file' id='imgFile' name='imgFile'>");
        fileDiv.append("<br>World (.jgw/.tfw):<br>")
        fileDiv.append("<input type='file' name='worldFile'><br>");
        fileDiv.append("Projection: ");
        var projSel = $("<select name=imgProj>");
        projSel.append("<option value='EPSG:3338'>Alaska Albers</option>");
        projSel.append("<option value='U'>UTM</option>");
        fileDiv.append(projSel);
    }
}

function locSelectChanged() {
    //"this" should be the map select pull-down
    var sel = $(this).find('option:selected');
    var loc = sel.data('loc');
    map.setView([loc[0], loc[1]], loc[2]);
}

function zoomToBounds(bounds) {
    var promise = $.Deferred();
    map.once("moveend zoomend", function() {
        setTimeout(function() {
            promise.resolve();
        }, 20);
    });
    map.fitBounds.call(map, bounds);
    return promise;
}

function setBounds() {
    var N = Number($('#maxLat').val());
    var S = Number($('#minLat').val());
    var E = Number($('#maxLon').val());
    var W = Number($('#minLon').val());
    if (W > E) {
        W -= 360; //make less than -180
    }
    map.off("moveend", updateBounds);
    map.off("zoomend", updateBounds);
    zoomToBounds([
        [S, W], //South-West corner
        [N, E] //North-East corner
    ]).then(function() {
        map.on("moveend", updateBounds);
        map.on("zoomend", updateBounds);
    });
}

function sizeMap() {
    var width = $('#mapWidth').val();

    var height = $('#mapHeight').val();

    var ratio = width / height;

    var contWidth = $('#mapContainer').width();
    var contHeight = $('#mapContainer').height();

    var padding = 10

    var targetWidth = contWidth - padding;
    var targetHeight = targetWidth / ratio

    if (targetHeight > contHeight) {
        targetHeight = contHeight - padding;
        targetWidth = targetHeight * ratio;
    }

    $('#topMap').css('width', targetWidth);
    $('#topMap').css('height', targetHeight);

    if (map !== null) {
        map.invalidateSize(true);
    }

    if ($('#lockWidth').is(':checked')) {
        $('#overviewWidth').val(Math.round(width / overviewRatio));
    }

    setTimeout(fixMarkerSize,100);
    setTimeout(setOverviewDiv, 250);
}

function fixMarkerSize(replot){
    if(typeof(replot)=='undefined'){
        replot=true
    }

    const contWidth = $('#mapContainer').width();
    const contHeight = $('#mapContainer').height();

    const dx=Math.abs(contWidth-BASE_MAP_SIZE[0]);
    const dy=Math.abs(contHeight-BASE_MAP_SIZE[1]);

    const xscale=contWidth/BASE_MAP_SIZE[0];
    const yscale=contHeight/BASE_MAP_SIZE[1];

    // compare relative change from 100% between x and y
    if(Math.abs(xscale-1)<Math.abs(yscale-1)){
        //x has a smaller percentage change than y, so use X
        scale=xscale
    }
    else{
        // either y had a smaller percentage change, or they are the same.
        scale=yscale;
    }

    const markerSize=BASE_MARKER_SIZE*scale;
    const fontSize=BASE_FONT_SIZE*scale;

    //no need for jquery here...
    const root=document.querySelector(':root');
    root.style.setProperty('--markerFontSize',`${fontSize}px`);

    //set the new size for the markers, and redraw.
    ICON_SIZE=markerSize;
    if(replot===true)
        plotMarkers();
}

var insetId = 0;
var insetMaps = {};

function addNewMap() {
    insetId += 1;
    var mapDiv = $('<div class="insetMap User"></div>');
    mapDiv.data('mapID', insetId);
    var titleDiv = $(`<div class=insetTitle>Inset ${insetId}</div>`);
    var innerMap = $('<div class=insetInner>')

    mapDiv.append(titleDiv);
    mapDiv.append(innerMap);
    var mapID = `insetMap${insetId}`
    innerMap.prop('id', mapID);

    var mapWidth = $('#maps').width() / 3;
    var mapHeight = $('#maps').height() / 3;
    mapDiv.css('width', mapWidth);
    mapDiv.css('height', mapHeight);
    mapDiv.css('top', '5px');
    mapDiv.css('left', '5px');

    $('#maps').append(mapDiv);

    var mapTiles = L.tileLayer('https://basemap.nationalmap.gov/ArcGIS/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}');
    var insetMap = L.map(mapID, {
        tap: false,
        zoomSnap: 0,
        layers: [mapTiles]
    })

    $(insetMap).data('MapID', insetId);

    insetMaps[insetId] = insetMap;

    var insetSettings = $('<div class="insetSettings">')
    insetSettings.data('mapID', insetId);
    insetSettings.append(`<div class="insetSettingsTitle">Inset ${insetId}</div>`);
    insetSettings.append('<button type=button class="deleteInset">Delete</button>');
    insetSettings.append(`<input type="hidden" id="insetBounds${insetId}" name="insetBounds">`);
    insetSettings.append(`<input type="hidden" id="insetZoom${insetId}" name="insetZoom">`);
    insetSettings.append(`<input type="hidden" id="insetLeft${insetId}" name="insetLeft">`);
    insetSettings.append(`<input type="hidden" id="insetTop${insetId}" name="insetTop">`);
    insetSettings.append(`<input type="hidden" id="insetWidth${insetId}" name="insetWidth">`);
    insetSettings.append(`<input type="hidden" id="insetHeight${insetId}" name="insetHeight">`);
    insetSettings.append
    $('#insetMaps').append(insetSettings);

    insetMap.on("moveend zoomend", function() {
        const insetID = $(this).data('MapID');
        updateInsetBounds(insetID);
    });

    insetMap.fitBounds(map.getBounds());

    //Set up mapDiv for moving/resizing
    mapDiv.draggable({
            containment: "parent",
            handle: "div.insetTitle",
            stop: updateInsetPosition
        })
        .resizable({
            containment: '#maps',
            zIndex: 1001,
            stop: updateInsetSize
        });

    updateInsetSize.call(mapDiv[0]);
}

function updateInsetSize(event, ui) {
    var height = $(this).find('div.insetInner').height();

    if (typeof(ui) !== 'undefined') {
        var width = ui.size['width'];
        var insetID = ui.helper.closest('div.insetMap.User').data('mapID');
    } else {
        //this should be the div in question
        var width = $(this).width();
        var insetID = $(this).data('mapID');
    }

    var percentWidth = width / $('#maps').width();
    var percentHeight = height / $('#maps').height();
    var unitWidth = Number($('#mapWidth').val()) * percentWidth;
    var unitHeight = Number($('#mapHeight').val()) * percentHeight;

    $(`#insetWidth${insetID}`).val(unitWidth);
    $(`#insetHeight${insetID}`).val(unitHeight);

    insetMaps[insetID].invalidateSize();
    updateInsetPosition.call(this, [event, ui]);
}

function updateInsetPosition(event, ui) {
    if (typeof(ui) !== 'undefined') {
        var top = ui['position']['top'];
        var left = ui['position']['left'];
        var insetID = ui.helper.closest('div.insetMap.User').data('mapID');
    } else {
        var top = $(this).position().top;
        var left = $(this).position().left;
        var insetID = $(this).data('mapID');
    }

    //1- to invert, since gmt is bottom left, not top left
    var percentTop = 1 - top / $('#maps').height();
    var percentLeft = left / $('#maps').width();

    var unitTop = Number($('#mapHeight').val()) * percentTop;
    var unitLeft = Number($('#mapWidth').val()) * percentLeft;

    $(`#insetTop${insetID}`).val(unitTop);
    $(`#insetLeft${insetID}`).val(unitLeft);

    updateInsetBounds(insetID); //for good measure
}

function updateInsetBounds(inset_id) {
    var bounds = insetMaps[inset_id].getBounds();
    var zoom = insetMaps[inset_id].getZoom();

    $(`#insetBounds${inset_id}`).val(bounds.toBBoxString());
    $(`#insetZoom${inset_id}`).val(zoom);
}

function removeInsetMap() {
    var settingsDiv = $(this).closest('div.insetSettings')
    var mapID = settingsDiv.data('mapID');
    $(`#insetMap${mapID}`).closest('div.insetMap.User').remove();
    settingsDiv.remove();
}

function updateBounds() {
    var bounds = map.getBounds();
    $('#mapBounds').val(bounds.toBBoxString());
    $('#mapZoom').val(map.getZoom());

    var N = Math.round(bounds.getNorth() * 1000) / 1000;
    var S = Math.round(bounds.getSouth() * 1000) / 1000;
    var E = Math.round(bounds.getWest() * 1000) / 1000;
    while (E < -180) {
        E += 360
    }
    var W = Math.round(bounds.getEast() * 1000) / 1000;
    while (W < -180) {
        W += 360;
    }

    $('#minLat').val(S);
    $('#maxLat').val(N);
    $('#minLon').val(E);
    $('#maxLon').val(W);
}

var req_id = null;

function updateStatus(payload) {
    if (typeof(payload) == 'object') {
        var stat = payload['status'];
        $('#progBar').val(payload['progress']);
    } else {
        var stat = payload;
        if (stat == "COMPLETE") {
            url = `getMap?REQ_ID=${req_id}`;
            window.location.href = url;
            closeStatus(5000);
        } else if (stat == "ERROR") {
            alert("Unable to generate map. A server error occured");
            closeStatus();
        }
        $('#progBar').removeAttr('value');
    }
    $('#downloadStatus').html(stat);
}

function closeStatus(delay) {
    if (typeof(delay) === 'undefined') {
        delay = 0;
    }

    monitorSocket.close();
    if (delay > 0)
        setTimeout($('#downloading').hide, delay);
    else
        $('#downloading').hide

    req_id = null;
}

function checkDownloadStatus() {
    return;
    if (req_id === null) {
        $('#downloading').hide();
        return; //no request
    }

    $.getJSON('checkstatus')
        .done(function(resp) {
            if (resp['done']) {
                url = `getMap?REQ_ID=${req_id}`;
                window.location.href = url;
                $('#downloading').hide();
                monitorSocket.close();
                req_id = null;
                return
            }
            var payload = resp['status'];
            updateStatus(payload);

            setTimeout(checkDownloadStatus, 2000); //Check again in 2 seconds.
        })
        .fail(function(jqXHR, textStatus, errorThrown) {
            alert("Unable to check status of download request. Please try again later.");
            $('#downloading').hide();
            monitorSocket.close();
        });
};

function getMap() {
    //make sure our bounds are up-to-date
    updateBounds();

    //setCookie("DownloadComplete", "0", 240);
    if ($('#imgFile').val() !== '')
        $('#downloadStatus').text("Uploading images...");
    else
        $('#downloadStatus').text("Requesting...");
    $('#downloading').css('display', 'grid');

    init_socket();
}

function xhrFunc() {
    var xhr = new window.XMLHttpRequest();
    xhr.upload.addEventListener("progress",
        updateUploadPercent,
        false
    );
    return xhr;
}

function init_socket() {
    var socketURL = 'wss://';
    if (location.protocol !== 'https:')
        socketURL = 'ws://';

    var host = location.hostname;
    var port = location.port;
    var path = location.pathname;
    socketURL+=host
    if(port!==''){
        socketURL+=`:${port}`
    }
    socketURL+=`${path}monitor/`

    monitorSocket = new WebSocket(socketURL)
    monitorSocket.onmessage = function(msg) {
        if (msg.data == 'PONG') {
            return;
        }

        var data = JSON.parse(msg.data);
        if (data.type == 'socketID') {
            var socketID = data.content;
            console.log(socketID);

            $('#socketID').val(socketID);
            //use a small timeout so the waiting dialog can be displayed immediately
            setTimeout(runGetMap, 50);
        } else if (data.type == 'status') {
            var status = data.content;
            updateStatus(status);
        }
    }
    monitorSocket.onopen = function() {
        pingTimer = setInterval(function() {
            monitorSocket.send('PING') //kepalive. Send ping every 5 seconds.
        }, 5000)
    }
    monitorSocket.onclose = function() {
        console.log("Web socket closed");
        if (pingTimer !== null) {
            clearInterval(pingTimer);
            pingTimer = null;
        }
    }
}

function runGetMap() {
    var formData = new FormData($('#setupForm')[0]);
    ajax_opts = {
        url: 'getMap',
        method: 'POST',
        data: formData,
        processData: false,
        contentType: false,
        cache: false,
    }

    if ($('#imgFile').val() !== '') {
        ajax_opts['xhr'] = xhrFunc;
    }

    $.ajax(ajax_opts)
        .done(function(resp) {
            req_id = resp
            console.log(resp);
            checkDownloadStatus();
        })
        .fail(function(jqXHR, textStatus, errorThrown) {
            alert(`Unable to request map. Server returned code ${jqXHR.status}, error: ${errorThrown}`);
            $('#downloading').hide();
        });

}

function updateUploadPercent(evt) {
    if (evt.lengthComputable) {
        var pc = (evt.loaded / evt.total) * 100
        pc = Math.round(pc * 10) / 10;
        if (pc >= 100) {
            $('#downloadStatus').text("Waiting for server...");
            return;
        }
        $('#progBar').val(pc);
        $('#downloadStatus').text("Uploading images...");
    }
}

function getStationsDebounce() {
    if (staTimer !== null) {
        clearTimeout(staTimer);
    }
    staTimer = setTimeout(getStations, 500);
}

var urlBase = 'https://volcanoes.usgs.gov';
var instrumentUrl = `${urlBase}/vsc/api/instrumentApi/data`;
const volcUrl=`${urlBase}/vsc/api/volcanoApi/regionstatus`;

let all_stations = [];
let all_volcs=[];
let usgs_cats = {};

function getStations() {
    if (staTimer !== null) {
        clearTimeout(staTimer);
    }
    staTimer = null;

    var bounds = map.getBounds();

    var minLat = bounds.getSouth();
    var maxLat = bounds.getNorth();
    var westLon = bounds.getWest();
    while (westLon < -180) {
        westLon += 360;
    }
    var eastLon = bounds.getEast();
    while (eastLon < -180) {
        eastLon += 360
    }

    all_stations = [];
    all_volcs=[];

    var westLon2 = null;
    var eastLon2 = null;
    if (westLon > eastLon) {
        westLon2 = westLon;
        eastLon2 = 180;
        westLon = -180
    }

    query_volcs(minLat,maxLat,eastLon,westLon,eastLon2,westLon2);
    query_stations(minLat, maxLat, eastLon, westLon, eastLon2, westLon2);

    //plot markers
    plotMarkers()
}

function query_volcs(minLat, maxLat, eastLon, westLon, eastLon2, westLon2){
    var url=`${volcUrl}?lat1=${minLat}&long1=${westLon}&lat2=${maxLat}&long2=${eastLon}`;
    $.getJSON(url)
    .done(function(data){
        //filter volcanoes to only show historically active
        if (typeof(ACTIVE_VOLCS) !== 'undefined' && ACTIVE_VOLCS.size>0){
            data=data.filter(volc=>ACTIVE_VOLCS.has(volc.vName))
        }
        all_volcs=all_volcs.concat(data);
        if (westLon2 !== null && eastLon2 !== null) {
            query_volcs(minLat, maxLat, eastLon2, westLon2, null, null);
        }
    })
}

function trackUnchecked(){
    const value=JSON.parse(this.value);
    const identStr=`${value['lat']}_${value['lon']}_${value['category']}`;
    if($(this).is(':checked')){
        const itemIdx=uncheckedMarkers.indexOf(identStr);
        if(itemIdx>=0){
            uncheckedMarkers.splice(itemIdx,1);
        }
    }
    else{
        uncheckedMarkers.push(identStr);
    }
}

function getChecked(parent){
    if(typeof(parent)==='undefined'){
        parent='';
    }

    const inputs=$(parent+' .staCheck:checked');
    let locs=[]
    inputs.each(function(idx,input){
        let value=JSON.parse(this.value);
        let lat=value['lat'];
        let lng=value['lon'];
        let cat=value['category'];
        let name=value['name'];

        let itemObj={
            lat:lat,
            lng:lng,
            cat:cat,
            name:name
        }
        locs.push(itemObj);
    })

    return locs
}

function removeCustomPosition(){
    //"this" is a station checkbox, thanks to jQuery
    const checkVal=JSON.parse(this.value);
    const markerName=checkVal['name'];
    delete checkVal['offx'];
    delete checkVal['offy'];
    delete customLabelLocs[markerName];
    this.value=JSON.stringify(checkVal);
}

function labelLocationChanged(){
    const parent=$(this).data('parent');
    $(parent+' .staCheck').each(removeCustomPosition);
    plotMarkersRun();
}

let staIcons={};
function refreshStationMarkerOpts(){
    //generate a color/icon array for the station markers
    $('#staMarkerStyle').remove(); //get rid of any old styles, as they may have changed.
    const staStyle=$('<style id="staMarkerStyle" type="text/css">');
    staIcons={};
    $('#staOpts tbody tr.staIconOpts').each(function(){
        const markerClass=$(this).data('markerclass');
        let svg=icon_images[$(this).find('input.staOpt_Icon').val()];
        if(typeof(svg)=='undefined'){
            //set a default icon if none specified for this type
            svg=icon_images['i']();
        }
        else if(typeof(svg)=='function'){
            svg=svg();
        }

        staIcons[markerClass]=svg;
        const colorSelector=$(this).find('td.color input')
        if(!colorSelector.hasClass('hidden')){
            const css=`
.${markerClass} path {fill:${colorSelector.val()};}
            `;
            staStyle.append(css);
        }
    })
    $('head').append(staStyle);
}

let markerPlotTimer=null;
function plotMarkers(){
    if(markerPlotTimer!=null){
        clearTimeout(markerPlotTimer);
    }

    markerPlotTimer=setTimeout(plotMarkersRun,100);
}

const toolTipOpts={
    permanent:true,
    className: "markerNameLabel",
    opacity:1,
    fill:false,
    fillColor:'#0F0',
    interactive:true,
    autoPan:false,
    closeButton:false,
    autoClose:false,
    closeOnEscapeKey:false,
    closeOnClick:false,
    offset:[0,0]
}

const labelOpts={
    interactive:true,
    className:"markerLabel",

}
//use a debounce timer on this so it doesn't get triggered many times when checking/unchecking all
function plotMarkersRun(){
    markerPlotTimer=null;
    refreshStationMarkerOpts();
    fixMarkerSize(false);

    //clear out tracking lists
    const toRemove=volcanoMarkers.concat(volcanoTooltips,stationMarkers,stationTooltips);

    for(const i in toRemove){
        let layer=toRemove[i];
        if(typeof(layer)!=='undefined'){
            map.removeLayer(layer);
        }
    }

    volcanoMarkers=[];
    volcanoTooltips=[];
    stationMarkers=[];
    stationTooltips=[];
    
    const volcsUseColor=$('#showVolcColor').is(':checked')

    let volcOffset,volcDir,staOffset,staDir,labelOffset,labelDir;
    let markerClass,svg
    const volcLabelPos=labelOffsets[$('#volcLabelLocation').val()];
    const staLabelPos=labelOffsets[$('#staLabelLocation').val()];

    if(typeof(volcLabelPos)!='undefined'){
        [volcOffset,volcDir]=volcLabelPos;
        volcOffset=[volcOffset[0]*scale,volcOffset[1]*scale];
    }

    if(typeof(staLabelPos)!='undefined'){
        [staOffset,staDir]=staLabelPos;
        staOffset=[staOffset[0]*scale,staOffset[1]*scale];
    }

    const checkedItems=$('.staCheck:checked');
    $(checkedItems).each(function(){
        let marker,labelPos,svgIcon;
        const itemInfo=JSON.parse(this.value);
        let itemCat=itemInfo['category'];
        if(typeof(itemCat)=='object'){
            itemCat=itemCat.type;
        }

        //only applies to volcanoes, stations/markers will be undefined.
        let isVolc=false;
        try{
            isVolc=itemCat.startsWith('volcano');
        } catch {
            // Not a string, so not a volcano.
            isVolc=false;
        }

        const color=volcsUseColor? itemCat : 'volcanoWHITE';

        //For some reason, leaflet wants items on the far side of the 
        //dateline to be more negitive.
        let lng=Number(itemInfo['lon']);
        if(lng>0){
            lng-=360;
        }
        const latlng=[Number(itemInfo['lat']), lng];
        let labelClass="markerLabel";

        if(isVolc){
            labelPos=volcLabelPos;
            labelOffset=volcOffset;
            labelDir=volcDir;
            markerClass=`marker${color}`
            svg=icon_images['t']()
        }
        else{
            labelPos=staLabelPos;
            labelOffset=staOffset;
            labelDir=staDir;
            labelClass="markerLabel stationLabel"
            markerClass=`staMarker${itemCat}`;
            svg=staIcons[markerClass]
        }
        
        svgIcon=L.divIcon({
            html:svg,
            className:markerClass,
            iconSize:[ICON_SIZE,ICON_SIZE],
            iconAnchor:[ICON_SIZE/2,ICON_SIZE/2]
        })

        marker=new L.Marker(latlng,
            {icon:svgIcon}
        )
        marker.addTo(map);

        if(isVolc){
            volcanoMarkers.push(marker);
        }else{
            stationMarkers.push(marker);
        }

        if(typeof(labelPos)!='undefined'){
            const markerType=isVolc?'volc':'sta'
            const checkID=computeItemID(itemInfo['name'],markerType)
            const itemCheck=$('#'+checkID);
            const checkVal=JSON.parse(itemCheck.val());
            const custX=checkVal['offx'];
            const custY=checkVal['offy'];
            let itemOffset=[labelOffset[0],labelOffset[1]];
            if(typeof(custX)!=='undefined' && typeof(custY)!=='undefined'){
                customLabelLocs[checkVal['name']]=[custX,custY];
                itemOffset=[
                    Number(custX)*scale+labelOffset[0],
                    Number(custY)*scale+labelOffset[1]
                ];
            }

            const labelIcon=L.divIcon({
                html:itemInfo['name'],
                className:labelClass,
                iconSize:'auto'
            })

            const label=new L.Marker(latlng,
                {
                    icon:labelIcon,
                    interactive:true,
                    draggable:true
                }
            )
            .addTo(map);

            $(label).data('checkID',checkID);

            // Currently marker is positioned at the same point as the volcano
            // we want to offset that somewhat.
            let pos=map.latLngToLayerPoint(label.getLatLng()); //top-left corner
            pos['x']+=itemOffset[0];
            pos['y']+=itemOffset[1];
            
            // Reference point is top-left corner, so to center or right justify text
            // we need to offset the X position by a percentage of the label width.
            if(labelDir=="center"){
                pos['x']-=($(label.getElement()).width()/2);
            }else if(labelDir=="right"){
                pos['x']-=$(label.getElement()).width();
            }

            const newPos=map.layerPointToLatLng(pos);
            label.setLatLng(newPos);

            //declared inline so we get pos as a closure variable
            label.on('dragend',function(event){
                const volcCheck=$('#'+checkID);
                const checkVal=JSON.parse(volcCheck.val());
                let newLatLon=this.getLatLng();

                checkVal['labelLat']=newLatLon['lat'];
                checkVal['labelLon']=newLatLon['lng'];

                //get the old offset
                let offx=checkVal['offx'] || 0;
                let offy=checkVal['offy'] || 0;

                //multiply by the scale factor to get the scaled offset
                offx*=scale;
                offy*=scale;

                //add in the latest move
                const dx=event.sourceTarget._newPos.x-pos.x
                const dy=event.sourceTarget._newPos.y-pos.y

                offx+=dx;
                offy+=dy;
                checkVal['offx']=offx/scale;
                checkVal['offy']=offy/scale;
                
                volcCheck.val(JSON.stringify(checkVal));

                setTimeout(calcLineAnchor,500,[this]);
            });

            // add or update the lat/lon stored in this item to 
            // the label position rather than the station position.
            checkVal['labelLat']=newPos['lat'];
            checkVal['labelLon']=newPos['lng'];
            itemCheck.val(JSON.stringify(checkVal));

            //unset the top/bottom CSS so the label actually shows up at the position it is set to.
            //container.css('top','').css('bottom','');

            if(isVolc){
                volcanoTooltips.push(label);
            }
            else{
                stationTooltips.push(label);
            }

            setTimeout(calcLineAnchor,500);
        }
    })
}

function calcLineAnchor(elements){
    if(typeof(elements)=='undefined'){
        elements=stationTooltips.concat(volcanoTooltips);
    }
    $(elements).each(function(){
        //this is a label "marker"
        const LabelLatLon=this.getLatLng();
        const topLeft=map.latLngToLayerPoint(LabelLatLon);

        const checkID=$(this).data('checkID');
        const itemCheck=$('#'+checkID);
        if(itemCheck.length==0){
            return false;
        }
        const checkVal=JSON.parse(itemCheck.val());
        const originLatLon=L.latLng(checkVal['lat'], checkVal['lon']);
        const originPos=map.latLngToLayerPoint(originLatLon);

        const width=$(this._icon).width();

        // subtract a constant from the height to keep the line from being
        // too far down on the final render.
        const height=$(this._icon).height();

        const leftX=topLeft.x;
        const centerX=leftX+(width/2);
        const rightX=leftX+width;
        let topY=topLeft.y;
        let bottomY=topY+height;
        const centerY=topY+(height/2)

        let anchorX=centerX;
        let anchorY=topY;

        const y_inset=4;
        if(leftX>originPos.x){
            anchorX=leftX;
            topY+=y_inset;
            bottomY-=y_inset;
        }else if(rightX<originPos.x){
            anchorX=rightX;
            topY+=y_inset;
            bottomY-=y_inset;
        }

        //Positive Y is down
        if(bottomY>originPos.y && topY<originPos.y){
            anchorY=centerY;
        } else if(bottomY<originPos.y){
            anchorY=bottomY;
        }

        const anchorPos=L.point(anchorX,anchorY);
        const anchorLatLon=map.layerPointToLatLng(anchorPos);

        checkVal['anchorLat']=anchorLatLon.lat;
        checkVal['anchorLon']=anchorLatLon.lng;
        itemCheck.val(JSON.stringify(checkVal));
    })
}

function query_stations(minLat, maxLat, eastLon, westLon, eastLon2, westLon2) {
    var url = `${instrumentUrl}?lat1=${minLat}&long1=${westLon}&lat2=${maxLat}&long2=${eastLon}`;
    $.getJSON(url)
        .done(function(data) {
            all_stations = all_stations.concat(data['instruments']);
            const categories=data['categories'];
            for(let i=0;i<categories.length;i++){
                const cat=categories[i];
                const catName=cat['category'];
                const catID=cat['catId'];
                const iconURL=cat['iconFullUrl'];
                usgs_cats[catID]={
                    'type':catName,
                    'iconURL':iconURL
                }
            }
            if (westLon2 !== null && eastLon2 !== null) {
                query_stations(minLat, maxLat, eastLon2, westLon2, null, null);
            } else {
                addCSVStations();
            }
        });
}

let csvStations=[]

function addCSVStations() {
    csvStations=[] //start with an empty list of CSV stations
    var file = $('#addStationCSV')[0].files;
    if (file.length == 0) {
        $('#clearStationCSV').hide();
        displayStations();
        return;
    }

    file = file[0];
    var reader = new FileReader();
    reader.onload = function() {
        var data = $.csv.toArrays(reader.result);
        for (var i = 1; i < data.length; i++) {
            var station = data[i];

            var staDict = {
                'station': station[2],
                'catId': station[3],
                'lat': station[0],
                'long': station[1],
            }

            if (!(station[3] in staCategories)) {
                staDict['catId'] = 999; //user defined/unknown
            }

            csvStations.push(staDict);
        }
        
        $('#clearStationCSV').show();
        displayStations();
    }
    reader.readAsBinaryString(file);
}

function displayVolcs(){
    const dest=$('#volcanoListTop').empty();

    //we don't really need the seenCodes list at this point, but I'm
    // leaving it in just in case we get an unexpected code.
    let seenCodes=[];

    //create divs for the expected codes, in the proper order.
    ['RED','ORANGE','YELLOW','GREEN','UNASSIGNED'].forEach(function(code,idx,codes){
        createGroupDiv(code,code,dest,'volc');
        seenCodes.push(code);
    })

    for(let i=0;i<all_volcs.length;i++){
        let volc=all_volcs[i];
        if(!volc['obs']=='avo'){
            continue;
        }

        let code=volc['colorCode'];
        if(seenCodes.indexOf(code)==-1){
            createGroupDiv(code,code,dest,'volc');
            seenCodes.push(code);
        }

        createVolcDiv(volc);
    }

    //remove any empty color divs
    const GROUP_DIVS=$('div.stationType.volcStation')
    GROUP_DIVS.filter(x=>$(GROUP_DIVS[x]).find('div.volc').length==0).remove();
}

function displayStations() {
    displayVolcs();
    var seenStations = []
    var seenCategories = []

    $('#stationListTop').empty();
    let displayStations=all_stations.concat(csvStations);

    for (var i = 0; i < displayStations.length; i++) {
        var sta = displayStations[i];
        var staName = sta['station'];
        if (seenStations.indexOf(staName) !== -1) {
            continue //already seen this station
        }
        seenStations.push(staName);

        var catID = sta['catId'];
        var cat = staCategories[catID] || usgs_cats[catID];

        if (seenCategories.indexOf(catID) == -1) {
            createGroupDiv(cat, catID, $('#stationListTop'),'sta');
            seenCategories.push(catID);
        }

        createStationDiv(sta, cat);
    }

    //check all by default
    $('.sectionSelectAll').each(function(){
        this.checked=true;
        toggleStations.call(this);
    });

    //make sure the map size is correct
    sizeMap();
}

function computeItemID(volc_name,type){
    return type+'Marker_'+volc_name.replace(/[^a-zA-Z0-9]/g,'');
}

function createVolcDiv(volc) {
    var info = {
        'lat': volc['lat'],
        'lon': volc['long'],
        'name': volc['vName'],
        'category': `volcano${volc['colorCode']}`
    }
    var destID = `volcCat${volc['colorCode']}`;
    createMarkerDiv(info,destID,"volc");
}

function createStationDiv(sta, cat) {
    var info = {
        'lat': sta['lat'],
        'lon': sta['long'],
        'name': sta['station'],
        'category': cat
    }
    var destID = `staCat${sta['catId']}`;
    createMarkerDiv(info,destID,"sta");
}

function createMarkerDiv(info,destID,classname){
    const markerName=info['name'];

    const custOffset=customLabelLocs[markerName];
    if(typeof(custOffset)!=='undefined'){
        info['offx']=custOffset[0];
        info['offy']=custOffset[1];
    }

    var div = $('<div>')
    .addClass(classname);

    var value = JSON.stringify(info);
    var checkbox = $('<input type="checkbox">')
    .addClass("staCheck")
    .attr("name","station")
    .attr('id',computeItemID(markerName,classname))
    .val(value);

    div.append(checkbox);
    div.append(markerName);

    $(`#${destID}`).append(div);
}

function createGroupDiv(group, id, dest,type) {
    var title = group;
    if (typeof title ==='object'){
        title=title['type'];
    }
    var divID = `${type}Cat${id}`
    var div = $(`<div class="stationType" id="${divID}">`);
    if(type=='volc'){
        div.addClass('volcStation')
    }
    var typeTitle = $('<div class=stationTypeHead>')
    var allCheck = $("<span class='leftEdge'>");
    allCheck.append("<input type=checkbox class='staCatAll'>");
    allCheck.append("All");
    typeTitle.append(allCheck);
    typeTitle.append(title);
    div.append(typeTitle);
    dest.append(div);
}

function toggleStations(reset) {
    var checked = false;
    if ($(this).is(':checked')) {
        checked = true;
    }

    $(this).closest('div.setupHeader').next('div.setupContent').find('input.staCheck').each(function() {
        const value=JSON.parse(this.value);
        const identStr=`${value['lat']}_${value['lon']}_${value['category']}`;
        let itemIdx=uncheckedMarkers.indexOf(identStr);
        if(checked){
            if(reset===true && itemIdx>=0){
                //if resetting, remove this item from the unchecked list
                uncheckedMarkers.splice(itemIdx,1);
                //no longer in list, so index is now -1
                itemIdx=-1;
            }
            if(itemIdx>=0){
                //this one should NOT be checked
                this.checked=false;
            }
            else{
                this.checked=true;
            }
        }
        else{ //checked=false
            this.checked = checked;
            if(itemIdx<0){
                uncheckedMarkers.push(identStr);
            }
        }
        checkForAll.call(this);
    })

    if($(this).closest('div.setupHeader').hasClass('markerSection')){
        plotMarkers();
    }
}

function toggleAll() {
    var checked = false;
    if ($(this).is(':checked')) {
        checked = true;
    }
    $(this).closest('div.stationType').find('input.staCheck').each(function() {
        const value=JSON.parse(this.value);
        const identStr=`${value['lat']}_${value['lon']}_${value['category']}`;
        const itemIdx=uncheckedMarkers.indexOf(identStr);
        if(checked){
            // if in the list, remove it
            if(itemIdx>=0){
                uncheckedMarkers.splice(itemIdx,1);
            }
        }
        else{
            // if not in the list, add it
            if(itemIdx<0){
                uncheckedMarkers.push(identStr);
            }
        }

        this.checked = checked;
    })

    if($(this).closest('div.setupContent').hasClass('markerSection')){
        plotMarkers();
    }

    //check if a higher level "all" checkbox needs to be checked.
    checkForAll.call(this);
}

function checkForAll() {
    if(!$(this).hasClass('staCatAll')){
        //only check the stationType div checkbox if this is not *already* a stationType div checkbox.
        var parent = $(this).closest('div.stationType');
        if (parent.find('input.staCheck').length == parent.find('input.staCheck:checked').length) {
            parent.find('input.staCatAll')[0].checked = true;
        } else {
            parent.find('input.staCatAll')[0].checked = false;
        }
    }

    var top = $(this).closest('div.setupContent');
    const header=top.prev('div.setupHeader');
    const selAll=header.find('input.sectionSelectAll');
    if (top.find('input.staCheck').length == top.find('input.staCheck:checked').length) {
       selAll[0].checked = true;
    } else {
        selAll[0].checked = false;
    }
}

function parseDataHeaders() {
    let file = $('#plotDataCSV')[0].files;
    if (file.length == 0) {
        return;
    }

    file = file[0];
    if (file.size > 1024) {
        //only read in the first 1KB of data at most to keep this fast
        file = file.slice(0, 1024);
    }

    var reader = new FileReader();
    reader.onload = function() {
        const data = $.csv.toArrays(reader.result);
        const header = data[0]

        const latSel = $('#latCol').empty();
        const lonSel = $('#lonCol').empty();
        const valSel = $('#valCol').empty();
        for (var i = 0; i < header.length; i++) {
            let option = `<option>${header[i]}</option>`;
            latSel.append(option);
            lonSel.append(option);
            valSel.append(option);
        }
    }
    reader.readAsBinaryString(file);
}
