#!/usr/bin/env python3
"""Calcul réel option B : isochrone ORS par établissement + somme WorldPop.

Usage:
  ORS_API_KEY=xxx python scripts/compute_access.py \
    --facilities facilities.csv --population sen_ppp_2020.tif \
    --minutes 30,60 --output public/results.json

Le CSV doit contenir nom, latitude et longitude (accents acceptés).
La colonne population du fichier WorldPop est un décompte de personnes par pixel.
"""
import argparse, json, os, sys, time
from pathlib import Path
import pandas as pd
import requests
import geopandas as gpd
from shapely.geometry import shape
from rasterstats import zonal_stats

ORS_URL = "https://api.openrouteservice.org/v2/isochrones/driving-car"

def col(df, names):
    for name in names:
        if name in df.columns: return name
    raise ValueError(f"Colonne absente. Attendues : {', '.join(names)}")

def get_isochrone(lon, lat, seconds, key):
    r = requests.post(ORS_URL, headers={"Authorization": key, "Content-Type": "application/json"},
        json={"locations": [[lon, lat]], "range": [seconds], "range_type": "time", "attributes": ["area"]}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"OpenRouteService ({r.status_code}) : {r.text[:300]}")
    return shape(r.json()["features"][0]["geometry"])

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--facilities", required=True); p.add_argument("--population", required=True)
    p.add_argument("--minutes", default="30,60"); p.add_argument("--output", default="public/results.json")
    p.add_argument("--profile", default="driving-car", choices=["driving-car","foot-walking"])
    a=p.parse_args(); key=os.getenv("ORS_API_KEY")
    if not key: sys.exit("ORS_API_KEY est obligatoire : https://openrouteservice.org/dev/#/signup")
    facilities=pd.read_csv(a.facilities); name=col(facilities,["nom","name","facility","etablissement"]); lat=col(facilities,["latitude","lat"]); lon=col(facilities,["longitude","lon","lng"])
    facilities=facilities.dropna(subset=[lat,lon]); gdf=gpd.GeoDataFrame(facilities, geometry=gpd.points_from_xy(facilities[lon],facilities[lat]), crs="EPSG:4326")
    minutes=[int(x) for x in a.minutes.split(",")]; results=[]
    print(f"{len(gdf)} points · {minutes} minutes · profil {a.profile}")
    for i,row in gdf.iterrows():
        item={"name":str(row[name]),"latitude":float(row[lat]),"longitude":float(row[lon]),"access":{},"zones":{}}
        for mins in minutes:
            # ORS accepte un profil dans l’URL ; éviter les appels silencieux sur un mauvais profil.
            url=ORS_URL.replace("driving-car",a.profile)
            resp=requests.post(url,headers={"Authorization":key,"Content-Type":"application/json"},json={"locations":[[float(row[lon]),float(row[lat])]],"range":[mins*60],"range_type":"time","attributes":["area"]},timeout=120)
            if resp.status_code!=200: raise RuntimeError(f"ORS {resp.status_code}: {resp.text[:300]}")
            polygon=shape(resp.json()["features"][0]["geometry"])
            stats=zonal_stats([polygon],a.population,stats=["sum"],nodata=0,all_touched=True)[0]
            item["access"][str(mins)]=int(round(stats.get("sum") or 0))
            item["zones"][str(mins)]=polygon.__geo_interface__
            time.sleep(.15)
        results.append(item); print(f"  ✓ {item['name']}")
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True)
    json.dump({"source":"WorldPop population count + OpenRouteService isochrones","profile":a.profile,"minutes":minutes,"generated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"facilities":results},open(out,"w"),ensure_ascii=False,indent=2)
    print(f"Résultats écrits dans {out}")
if __name__=="__main__": main()
