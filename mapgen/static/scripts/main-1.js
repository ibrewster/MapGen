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
    tiles.once('load', updateBounds);

    map = L.map('topMap', {
        "center": [58, -164],
        "zoom": 5,
        zoomSnap: 0,
        layers: [tiles]
    })

    map.on("moveend", updateBounds);
    map.on("zoomend", updateBounds);
    map.on("moveend zoomend", getStationsDebounce);

    $('.latLon').change(setBounds);
    $('.reload').click(updateBounds);
    $('#mapLocation').change(locSelectChanged);
    $('#overviewWidth').change(overviewWidthChanged);
    $('#overviewUnits').text($('#sizeUnits option:selected').text());
    $('#sizeUnits').change(function() {
        $('#overviewUnits').text($('#sizeUnits option:selected').text());
    });
    $('#overlayFormat').change(changeFileType);
    changeFileType();
});


function changeFileType() {
    var type = $('#overlayFormat').val();
    var fileDiv = $('#overlayFiles').empty();
    if (type == 't') {
        fileDiv.append("Image (.tiff):<br>");
        fileDiv.append("<input type='file' name='imgFile'>")
    } else if (type == 'j') {
        fileDiv.append('Image (.jpg/.tif):<br>')
        fileDiv.append("<input type='file' name='imgFile'>");
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

function overviewWidthChanged() {
    overviewRatio = $('#mapWidth').val() / $(this).val()
}

function sizeMap() {
    var width = $('#mapWidth').val();

    if ($('#lockWidth').is(':checked')) {
        $('#overviewWidth').val(Math.round(width / overviewRatio));
    }

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
}

function serialize(obj) {
    var str = [];
    for (var p in obj)
        if (obj.hasOwnProperty(p)) {
            str.push(encodeURIComponent(p) + "=" + encodeURIComponent(obj[p]));
        }
    return str.join("&");
}

var checkDownloadCookie = function() {
    if (getCookie("DownloadComplete") == "1") {
        setCookie("DownloadComplete", "0", 100); //Expiration could be anything... As long as we reset the value
        $('#downloading').hide();
    } else {
        downloadTimeout = setTimeout(checkDownloadCookie, 1000); //Re-run this function in 1 second.
    }
};

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

function getMap() {
    //make sure our bounds are up-to-date
    updateBounds();

    setCookie("DownloadComplete", "0", 240);
    $('#downloading').css('display', 'grid');
    setTimeout(checkDownloadCookie, 1000);
    setTimeout(runGetMap, 50);
}

function runGetMap() {
    $('#setupForm')[0].submit();
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