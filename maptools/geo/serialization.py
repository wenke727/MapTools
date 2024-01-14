import pandas as pd
import geopandas as gpd
from shapely import wkt


def read_csv_to_geodataframe(file_path, crs="EPSG:4326"):
    df = pd.read_csv(file_path)
    
    df['geometry'] = df['geometry'].apply(wkt.loads)
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs=crs)
    
    if "Unnamed: 0" in gdf.columns:
        gdf.drop(columns=['Unnamed: 0'], inplace=True)
    
    return gdf


if __name__ == "__main__":
    # Usage Example:
    gdf = read_csv_to_geodataframe('your_csv_file.csv')
    print(gdf)
