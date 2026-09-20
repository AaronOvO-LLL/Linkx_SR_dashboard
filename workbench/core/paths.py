import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, 'config')
PRODUCTS_DIR = os.path.join(CONFIG_DIR, 'products')
FIELD_PACKS_DIR = os.path.join(CONFIG_DIR, 'field_packs')
TEMPLATES_DIR = os.path.join(ROOT, 'templates')
STATIC_DIR = os.path.join(ROOT, 'static')
DATA_DIR = os.path.join(ROOT, 'data')
OUTPUT_DIR = os.path.join(DATA_DIR, 'outputs')
EXPORT_DIR = os.path.join(DATA_DIR, 'exports')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
SAMPLES_DIR = os.path.join(ROOT, 'samples')

for _d in (DATA_DIR, OUTPUT_DIR, EXPORT_DIR, UPLOAD_DIR, SAMPLES_DIR):
    os.makedirs(_d, exist_ok=True)


def product_dir(product_type):
    return os.path.join(PRODUCTS_DIR, product_type)


def artifact_template_dir(product_type):
    return os.path.join(product_dir(product_type), 'templates')
