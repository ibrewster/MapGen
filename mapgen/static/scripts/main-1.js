var map = null;
$(document).ready(function() {
    $(document).on('change', '.mapSize', sizeMap);
    $(window).resize(sizeMap);
    $('#getMap').click(getMap);

    sizeMap();

    var tiles = L.tileLayer('https://basemap.nationalmap.gov/ArcGIS/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}')

    map = L.map('topMap', {
        "center": [58, -164],
        "zoom": 5,
        zoomSnap: 0,
        layers: [tiles]
    })
})

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

function locSelectChanged() {
    //"this" should be the map select pull-down

}

function sizeMap() {
    var width = $('#mapWidth').val()
    var height = $('#mapHeight').val()

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

function getMap() {
    var width = $('#mapWidth').val();
    var height = $('#mapHeight').val();
    var unit = $('#sizeUnits').val();

    var bounds = map.getBounds().toBBoxString();

    var args = {
        'width': width,
        'height': height,
        'bounds': bounds,
        'unit': unit
    }

    var query = serialize(args)
    var dest = 'getMap?' + query;

    setCookie("DownloadComplete", "0", 240);
    $('#downloading').css('display', 'grid');
    setTimeout(checkDownloadCookie, 1000);
    setTimeout(function() { runGetMap(dest); }, 50);
}

function runGetMap(dest) {
    window.location.href = dest;
}