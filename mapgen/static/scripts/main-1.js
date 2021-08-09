var map = null;
var overviewRatio = 5;
var staTimer = null;

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
    $(document).on('click', '#stationSelAll', toggleStations);
    $(window).resize(sizeMap);
    $('#overviewWidth').change(function() { overviewChanged = true; })
    $('#getMap').click(getMap);

    sizeMap();

    var tiles = L.tileLayer('https://basemap.nationalmap.gov/ArcGIS/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}')


    map = L.map('topMap', {
        zoomSnap: 0,
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

    $('.latLon').change(setBounds);
    $('.reload').click(updateBounds);
    $('#resetOverview').click(resetOverview);
    $('#mapLocation').change(locSelectChanged);
    $('#overviewWidth').change(overviewWidthChanged);
    $('#overviewUnits').text($('#sizeUnits option:selected').text());
    $('#sizeUnits').change(function() {
        $('#overviewUnits').text($('#sizeUnits option:selected').text());
    });
    $('#overlayFormat').change(changeFileType);
    $(document).on('change', '#overview, #overviewWidth', setOverviewDiv);
    changeFileType();
    setOverviewDiv();
});

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
        setTimeout(setOverviewDiv, 250);
    }
}

function serialize(obj) {
    var str = [];
    for (var p in obj)
        if (obj.hasOwnProperty(p)) {
            str.push(encodeURIComponent(p) + "=" + encodeURIComponent(obj[p]));
        }
    return str.join("&");
}

function updateBounds() {
    var bounds = map.getBounds();
    $('#mapBounds').val(bounds.toBBoxString());
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

function checkDownloadStatus() {
    if (req_id === null) {
        $('#downloading').hide();
        return; //no request
    }

    $.getJSON('checkstatus/' + req_id)
        .done(function(resp) {
            if (resp['done']) {
                url = 'getMap/' + req_id;
                window.location.href = url;
                $('#downloading').hide();
                req_id = null;
                return
            }
            var payload = resp['status'];
            if (typeof(payload) == 'object') {
                var stat = payload['status'];
                $('#progBar').val(payload['progress']);
            } else {
                var stat = payload;
                $('#progBar').removeAttr('value');
            }
            $('#downloadStatus').html(stat);

            setTimeout(checkDownloadStatus, 2000); //Check again in 2 seconds.
        })
        .fail(function(jqXHR, textStatus, errorThrown) {
            alert("Unable to check status of download request. Please try again later.");
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

    //use a small timeout so the waiting dialog can be displayed immediately
    setTimeout(runGetMap, 50);
}

function xhrFunc() {
    var xhr = new window.XMLHttpRequest();
    xhr.upload.addEventListener("progress",
        updateUploadPercent,
        false
    );
    return xhr;
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
            alert(`Unable to request map. Server returned code ${textStatus}, error ${errorThrown}`);
        });

    //$('#setupForm')[0].submit();
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

var stationCategories = {};

function getStationsDebounce() {
    if (staTimer !== null) {
        clearTimeout(staTimer);
    }
    staTimer = setTimeout(getStations, 500);
}

var urlBase = 'https://volcanoes.usgs.gov';
var instrumentUrl = `${urlBase}/vsc/api/instrumentApi/data`;

function getStations() {
    if (staTimer !== null) {
        clearTimeout(staTimer);
    }
    staTimer = null;

    updateBounds();
    var minLat = $('#minLat').val();
    var maxLat = $('#maxLat').val();
    var minLon = $('#minLon').val();
    var maxLon = $('#maxLon').val();

    var url = `${instrumentUrl}?lat1=${minLat}&long1=${minLon}&lat2=${maxLat}&long2=${maxLon}`;
    $.getJSON(url)
        .done(function(data) {
            $('#stationListTop').empty();

            var cats = data['categories'];
            for (var i = 0; i < cats.length; i++) {
                var cat = cats[i];
                stationCategories[cat['catId']] = cat;
                createStationGroup(cat);
            }

            var stas = data['instruments'];
            for (var i = 0; i < stas.length; i++) {
                var sta = stas[i];
                var cat = stationCategories[sta['catId']];
                createStationDiv(sta, cat);
            }

            //check all by default
            $('#stationSelAll')[0].checked = true;
            toggleStations.call($('#stationSelAll')[0]);

            //make sure the map size is correct
            sizeMap();
        });
}

function createStationDiv(sta, cat) {
    var info = {
        'lat': sta['lat'],
        'lon': sta['long'],
        'name': sta['staton'],
        'icon': urlBase + cat['iconUrl']
    }

    var div = $('<div class="sta">')
    var value = JSON.stringify(info);
    var checkbox = $('<input type="checkbox" class="staCheck" name="station">');
    checkbox.val(value);
    div.append(checkbox);
    div.append(sta['station']);
    $(`#staCat${sta['catId']}`).append(div);
}

function createStationGroup(info) {
    var staType = info['category'];
    var div = $(`<div class="stationType" id="staCat${info['catId']}">`);
    var typeTitle = $('<div class=stationTypeHead>')
    var allCheck = $("<span class='leftEdge'>");
    allCheck.append("<input type=checkbox class='staCatAll'>");
    allCheck.append("All");
    typeTitle.append(allCheck);
    typeTitle.append(staType);
    div.append(typeTitle);
    $('#stationListTop').append(div);
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
    if (top.find('input.staCheck').length == top.find('input.staCheck:checked').length) {
        $('#stationSelAll')[0].checked = true;
    } else {
        $('#stationSelAll')[0].checked = false;
    }
}