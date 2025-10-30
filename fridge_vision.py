from ultralytics import YOLO
from PIL import Image
import os

def detect_ingredients(image_path):
    # Încarcă modelul YOLO pre-antrenat (pe COCO)
    model = YOLO("yolov8n.pt")

    # Rulează predicția
    results = model(image_path, verbose=False)

    # Extrage denumirile obiectelor detectate
    detected_classes = []
    for box in results[0].boxes.cls:
        cls_id = int(box)
        detected_classes.append(model.names[cls_id])

    # Elimină duplicatele
    ingredients = list(set(detected_classes))
    print(f"[INFO] Ingrediente detectate: {ingredients}")
    return ingredients
