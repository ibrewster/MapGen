from osgeo import osr


def get_extents(src, proj=None):
    ulx, xres, xskew, uly, yskew, yres = src.GetGeoTransform()
    lrx = ulx + (src.RasterXSize * xres)
    lry = uly + (src.RasterYSize * yres)

    src_srs = osr.SpatialReference()
    src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    if proj is not None:
        epsg_code = int(proj.replace('EPSG:', ''))
        src_srs.ImportFromEPSG(epsg_code)
    else:
        src_srs.ImportFromWkt(src.GetProjection())

    tgt_srs = src_srs.CloneGeogCS()

    transform = osr.CoordinateTransformation(src_srs, tgt_srs)
    # top-left, top-right,bottom-right,bottom-left
    corners = ((ulx, uly), (lrx, uly), (lrx, lry), (ulx, lry))
    trans_corners = transform.TransformPoints(corners)

    ulx, uly, _ = trans_corners[0]
    urx, ury, _ = trans_corners[1]
    lrx, lry, _ = trans_corners[2]
    llx, lly, _ = trans_corners[3]

    # figure out which X is to the left.
    # Make both upper and lower coordinates
    # negitive for easy comparison
    comp_upper = ulx
    comp_lower = llx

    if comp_upper > 0:
        comp_upper -= 360

    if comp_lower > 0:
        comp_lower -= 360

    if comp_upper < comp_lower:
        minx = ulx
    else:
        minx = llx

    comp_upper = urx
    comp_lower = lrx
    if comp_upper > 0:
        comp_upper -= 360

    if comp_lower > 0:
        comp_lower -= 360

    if comp_upper > comp_lower:
        maxx = urx
    else:
        maxx = lrx

    miny = min(lly, lry)
    maxy = max(uly, ury)

    if minx > maxx:
        minx -= 360

    return [minx, miny, maxx, maxy]
