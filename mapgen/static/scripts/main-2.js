var map = null;
var overviewRatio = 5;
var staTimer = null;
var monitorSocket = null;
var pingTimer = null;
var units = "i"

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
    $(document).on('click', '.sectionSelectAll', toggleStations);
    $(document).on('click', 'button.deleteInset', removeInsetMap);
    $(document).on('change', '#overview, #overviewWidth', setOverviewDiv);

    $('#overviewWidth').change(function() { overviewChanged = true; })
    $('#getMap').click(getMap);

    initMap();

    $(window).resize(sizeMap);
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
    $('#cmCancel').click(function(){
        $('#cmSelector').hide();
    });
    $('#dataTrans').change(function(){
        $('#transLevel').text($(this).val());
    })

    $('div.help').hover(showHelp,hideHelp);

    changeFileType();
    setOverviewDiv();
    getStationsDebounce();
    setupAccordion();
});

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
        if( header.hasClass('accordion-open')){
            return;
        }
        $('#setupInner div.setupContent').slideUp();
        $('div.accordion-open').removeClass('accordion-open');
        content.slideDown()
        header.addClass('accordion-open');
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
        layers: [tiles]
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
    console.log(disp_size);
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
    setTimeout(setOverviewDiv, 250);
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
            url = 'getMap';
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
                url = 'getMap';
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
        console.error("Web socket closed");
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
}

function query_volcs(minLat, maxLat, eastLon, westLon, eastLon2, westLon2){
    var url=`${volcUrl}?lat1=${minLat}&long1=${westLon}&lat2=${maxLat}&long2=${eastLon}`;
    $.getJSON(url)
    .done(function(data){
        //filter volcanoes to only show historically active
        if (typeof(ACTIVE_VOLCS) !== 'undefined'){
            data=data.filter(volc=>ACTIVE_VOLCS.has(volc.vName))
        }
        all_volcs=all_volcs.concat(data);
        if (westLon2 !== null && eastLon2 !== null) {
            query_volcs(minLat, maxLat, eastLon2, westLon2, null, null);
        }
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

function addCSVStations() {
    var file = $('#addStationCSV')[0].files;
    if (file.length == 0) {
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

            all_stations.push(staDict);
        }

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

    for (var i = 0; i < all_stations.length; i++) {
        var sta = all_stations[i];
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

function createVolcDiv(volc) {
    var info = {
        'lat': volc['lat'],
        'lon': volc['long'],
        'name': volc['vName'],
        'category': `volcano${volc['colorCode']}`
    }

    var div = $('<div class="volc">')
    var value = JSON.stringify(info);
    var checkbox = $('<input type="checkbox" class="staCheck" name="station">');
    checkbox.val(value);
    div.append(checkbox);
    div.append(volc['vName']);
    var destID = `volcCat${volc['colorCode']}`;
    $(`#${destID}`).append(div);
}

function createStationDiv(sta, cat) {
    var info = {
        'lat': sta['lat'],
        'lon': sta['long'],
        'name': sta['staton'],
        'category': cat
    }

    var div = $('<div class="sta">')
    var value = JSON.stringify(info);
    var checkbox = $('<input type="checkbox" class="staCheck" name="station">');
    checkbox.val(value);
    div.append(checkbox);
    div.append(sta['station']);
    var destID = `staCat${sta['catId']}`;
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

function toggleStations() {
    var checked = false;
    if ($(this).is(':checked')) {
        checked = true;
    }

    $(this).closest('div.setupHeader').next('div.setupContent').find('input.staCheck').each(function() {
        this.checked = checked;
        checkForAll.call(this);
    })
}

function toggleAll() {
    var checked = false;
    if ($(this).is(':checked')) {
        checked = true;
    }
    $(this).closest('div.stationType').find('input.staCheck').each(function() {
        this.checked = checked;
    })
}

function checkForAll() {
    var parent = $(this).closest('div.stationType');
    if (parent.find('input.staCheck').length == parent.find('input.staCheck:checked').length) {
        parent.find('input.staCatAll')[0].checked = true;
    } else {
        parent.find('input.staCatAll')[0].checked = false;
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