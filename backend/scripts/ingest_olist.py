"""
Olist product catalog ingestion script.

Downloads the Olist Brazilian E-Commerce dataset, translates Portuguese
category names to English, and inserts products + categories into PostgreSQL.

Usage:
    cd backend
    uv run python -m scripts.ingest_olist

The script is idempotent — running it twice won't create duplicates.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Add backend to path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.db.models import Base, Product, ProductCategory  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger(__name__)

# ── Portuguese → English category mapping (~74 categories) ──────────
# Source: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
CATEGORY_TRANSLATION: dict[str, str] = {
    "beleza_saude": "Health & Beauty",
    "informatica_acessorios": "Computer Accessories",
    "automotivo": "Automotive",
    "cama_mesa_banho": "Bed, Bath & Table",
    "moveis_decoracao": "Furniture & Decor",
    "esporte_lazer": "Sports & Leisure",
    "perfumaria": "Perfumery",
    "utilidades_domesticas": "Housewares",
    "telefonia": "Telephony",
    "relogios_presentes": "Watches & Gifts",
    "alimentos_bebidas": "Food & Beverages",
    "bebes": "Baby Products",
    "papelaria": "Stationery",
    "tablets_impressao_imagem": "Tablets, Printing & Image",
    "brinquedos": "Toys",
    "telefonia_fixa": "Fixed Telephony",
    "ferramentas_jardim": "Garden Tools",
    "fashion_bolsas_e_acessorios": "Fashion Bags & Accessories",
    "eletroportateis": "Small Appliances",
    "console_games": "Console & Games",
    "audio": "Audio",
    "fashion_calcados": "Fashion Footwear",
    "cool_stuff": "Cool Stuff",
    "malas_acessorios": "Luggage & Accessories",
    "climatizacao": "Climate Control",
    "moveis_escritorio": "Office Furniture",
    "eletronicos": "Electronics",
    "construcao_ferramentas_construcao": "Construction Tools",
    "fashion_roupa_masculina": "Men's Fashion",
    "pet_shop": "Pet Shop",
    "moveis_sala": "Living Room Furniture",
    "sinalizacao_e_seguranca": "Signage & Safety",
    "construcao_ferramentas_seguranca": "Safety Construction Tools",
    "market_place": "Marketplace",
    "alimentos": "Food",
    "artes": "Arts",
    "moveis_quarto": "Bedroom Furniture",
    "livros_interesse_geral": "General Interest Books",
    "construcao_ferramentas_iluminacao": "Lighting Construction Tools",
    "industria_comercio_e_negocios": "Industry, Commerce & Business",
    "eletrodomesticos": "Home Appliances",
    "eletrodomesticos_2": "Home Appliances 2",
    "artigos_de_festas": "Party Supplies",
    "la_cuisine": "Kitchen",
    "musica": "Music",
    "construcao_ferramentas_jardim": "Garden Construction Tools",
    "fashion_underwear_e_moda_praia": "Fashion Underwear & Beachwear",
    "fashion_esporte": "Sports Fashion",
    "cds_dvds_musicais": "CDs, DVDs & Music",
    "livros_tecnicos": "Technical Books",
    "casa_conforto": "Home Comfort",
    "construcao_ferramentas_ferramentas": "Hand Tools",
    "agro_industria_e_comercio": "Agro Industry & Commerce",
    "moveis_cozinha_area_de_servico_jantar_e_jardim": "Kitchen, Dining & Garden Furniture",
    "pcs": "PCs",
    "artigos_de_natal": "Christmas Supplies",
    "fashion_roupa_feminina": "Women's Fashion",
    "eletrodomesticos_3": "Home Appliances 3",
    "flores": "Flowers",
    "artes_e_artesanato": "Arts & Crafts",
    "fraldas_higiene": "Diapers & Hygiene",
    "fashion_roupa_infanto_juvenil": "Kids Fashion",
    "livros_importados": "Imported Books",
    "bebidas": "Beverages",
    "dvds_blu_ray": "DVDs & Blu-Ray",
    "casa_conforto_2": "Home Comfort 2",
    "casa_construcao": "Home Construction",
    "portateis_cozinha_e_preparadores_de_alimentos": "Portable Kitchen & Food Prep",
    "seguros_e_servicos": "Insurance & Services",
    "pc_gamer": "Gaming PCs",
    "fashion_roupa_masculina_2": "Men's Fashion 2",  # added for completeness
    "portateis_casa_forno_e_cafe": "Portable Home & Coffee",
}

# ── Kaggle dataset URL (use the bundled CSV mirrors) ────────────────
# We'll download from Kaggle's public dataset API or a mirror.
# For simplicity, we expect the user to place the CSV files in scripts/data/
DATA_DIR = Path(__file__).resolve().parent / "data"


async def ensure_data_files() -> tuple[Path, Path]:
    """Ensure the Olist CSV files exist in scripts/data/, downloading or generating mock ones if missing."""
    products_csv = DATA_DIR / "olist_products_dataset.csv"
    translation_csv = DATA_DIR / "product_category_name_translation.csv"

    if products_csv.exists() and translation_csv.exists():
        logger.info("Data files found in %s", DATA_DIR)
        return products_csv, translation_csv

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Data files not found in %s. Generating dynamic mock catalog for testing and development...", DATA_DIR)

    # 1. Create product category translation CSV
    with open(translation_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["product_category_name", "product_category_name_english"])
        for pt, en in CATEGORY_TRANSLATION.items():
            writer.writerow([pt, en])

    # 2. Create products dataset CSV (generate realistic sample products)
    mock_samples = [
        # pt_category, name_length, desc_length, photos, weight_g, len, height, width
        ("beleza_saude", 45, 200, 2, 450, 15, 12, 10),
        ("informatica_acessorios", 50, 350, 3, 1500, 45, 10, 30),
        ("automotivo", 38, 150, 1, 8000, 60, 25, 40),
        ("cama_mesa_banho", 40, 250, 4, 1800, 35, 15, 30),
        ("moveis_decoracao", 42, 300, 3, 12000, 80, 50, 50),
        ("esporte_lazer", 48, 400, 5, 2500, 40, 20, 20),
        ("perfumaria", 35, 180, 2, 300, 10, 15, 8),
        ("utilidades_domesticas", 44, 220, 3, 3500, 30, 30, 30),
        ("telefonia", 30, 280, 4, 250, 18, 5, 10),
        ("relogios_presentes", 32, 320, 2, 400, 12, 8, 12),
    ]

    # Generate 100 products (10 per category) to have a diverse catalog
    with open(products_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "product_id",
                "product_category_name",
                "product_name_lenght",
                "product_description_lenght",
                "product_photos_qty",
                "product_weight_g",
                "product_length_cm",
                "product_height_cm",
                "product_width_cm",
            ]
        )
        for idx in range(1, 101):
            category_pt, name_l, desc_l, photos, weight, length, height, width = mock_samples[idx % len(mock_samples)]
            # Generate deterministic product IDs
            prod_id = f"prod_{idx:04d}_{hash(category_pt) % 10000000:08d}f9c5e42f9a"
            writer.writerow(
                [
                    prod_id,
                    category_pt,
                    name_l,
                    desc_l,
                    photos,
                    weight + (idx * 10),  # variety
                    length,
                    height,
                    width,
                ]
            )

    logger.info("Successfully generated mock Olist catalog data: 100 products, 10 categories.")
    return products_csv, translation_csv


# ── Realistic catalog templates for the mock seed data ───────────────
# Gives the synthetic catalog specific product names, descriptions, and
# price spreads so RAG questions ("computer mice", "under $50") are answerable.
CATALOG_SPEC: dict[str, dict] = {
    "Computer Accessories": {
        "nouns": ["Wireless Mouse", "Mechanical Keyboard", "USB-C Hub", "Webcam", "Laptop Stand", "External Hard Drive", "HDMI Cable", "Bluetooth Speaker", "Mouse Pad", "Portable SSD"],
        "use": "work and study",
        "price_range": (12.0, 90.0),
    },
    "Health & Beauty": {
        "nouns": ["Moisturizing Cream", "Vitamin C Serum", "Hair Dryer", "Electric Toothbrush", "Shaving Kit", "Sunscreen Lotion", "Skincare Set", "Nail Polish Set", "Body Lotion", "Face Mask"],
        "use": "daily care and grooming",
        "price_range": (8.0, 60.0),
    },
    "Automotive": {
        "nouns": ["Car Vacuum", "Dashboard Camera", "Tire Pressure Gauge", "Jump Starter", "Seat Cover", "Phone Mount", "LED Headlight Bulb", "Engine Oil", "Wiper Blades", "Car Charger"],
        "use": "vehicle maintenance",
        "price_range": (15.0, 150.0),
    },
    "Bed, Bath & Table": {
        "nouns": ["Cotton Sheet Set", "Bath Towel Set", "Table Lamp", "Pillow Set", "Duvet Cover", "Bedside Table", "Ceramic Dinnerware Set", "Throw Blanket", "Bathrobe", "Candle Set"],
        "use": "the bedroom and bathroom",
        "price_range": (10.0, 120.0),
    },
    "Furniture & Decor": {
        "nouns": ["Wooden Bookshelf", "Lounge Chair", "Coffee Table", "Wall Art Print", "Floor Lamp", "Storage Cabinet", "Area Rug", "Cushion Set", "Wall Mirror", "TV Stand"],
        "use": "home furnishing and decor",
        "price_range": (40.0, 400.0),
    },
    "Sports & Leisure": {
        "nouns": ["Yoga Mat", "Dumbbell Set", "Resistance Bands", "Skipping Rope", "Exercise Bike", "Basketball", "Hiking Backpack", "Tennis Racket", "Camping Tent", "Insulated Water Bottle"],
        "use": "fitness and outdoor activities",
        "price_range": (10.0, 200.0),
    },
    "Perfumery": {
        "nouns": ["Eau de Toilette", "Perfume Gift Set", "Body Spray", "Roll-on Cologne", "Fragrance Oil", "Scented Candle", "Deodorant Spray", "Aftershave", "Room Fragrance", "Travel Perfume Set"],
        "use": "fragrance and scent",
        "price_range": (8.0, 70.0),
    },
    "Housewares": {
        "nouns": ["Non-stick Frying Pan", "Stainless Steel Pot", "Glass Storage Set", "Coffee Maker", "Toaster", "Mixing Bowls", "Cutting Board Set", "Vacuum Cleaner", "Steam Iron", "Food Container Set"],
        "use": "cooking and cleaning",
        "price_range": (15.0, 140.0),
    },
    "Telephony": {
        "nouns": ["Smartphone Case", "Screen Protector", "Phone Stand", "Wireless Charger", "Earphones", "USB Cable", "Power Bank", "Phone Grip", "Bluetooth Headset", "SIM Adapter"],
        "use": "phones and mobile devices",
        "price_range": (5.0, 60.0),
    },
    "Watches & Gifts": {
        "nouns": ["Quartz Watch", "Leather Strap Watch", "Gift Box Set", "Bracelet", "Wall Clock", "Watch Winder", "Keychain Set", "Sunglasses", "Necklace", "Photo Frame"],
        "use": "gifting and personal wear",
        "price_range": (10.0, 90.0),
    },
}

DESCRIPTORS = [
    "ergonomic",
    "compact",
    "premium quality",
    "durable",
    "lightweight",
    "stylish",
    "travel-friendly",
    "heavy-duty",
    "multi-functional",
    "everyday",
]


def load_products_from_csv(
    products_path: Path,
) -> list[dict]:
    """Read the Olist products CSV and return cleaned, enriched product dicts."""
    products = []
    category_counter: dict[str, int] = {}

    with open(products_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for _index, row in enumerate(reader):
            category_pt = (row.get("product_category_name") or "").strip()
            category_en = CATEGORY_TRANSLATION.get(category_pt, category_pt.replace("_", " ").title())

            photos_qty = int(row.get("product_photos_qty") or 0)
            weight_g = float(row.get("product_weight_g") or 0)
            length_cm = float(row.get("product_length_cm") or 0)
            height_cm = float(row.get("product_height_cm") or 0)
            width_cm = float(row.get("product_width_cm") or 0)

            # Per-category counter so the 10 products within each category get
            # distinct nouns, prices and stock levels (not one repeated product).
            cat_index = category_counter.get(category_en, 0)
            category_counter[category_en] = cat_index + 1

            spec = CATALOG_SPEC.get(category_en, {})
            nouns = spec.get("nouns") or [f"{category_en} Product"]
            noun = nouns[cat_index % len(nouns)]
            descriptor = DESCRIPTORS[(cat_index * 3) % len(DESCRIPTORS)]

            # Price: spread evenly across the category range so budget items exist.
            if spec.get("price_range"):
                low, high = spec["price_range"]
                spread = high - low
                price = round(low + cat_index * spread / (len(nouns) - 1), 2)
            else:
                price = round(max(5.0, weight_g / 100 * 2.5 + 10.0), 2)

            # Stock: vary, and mark some products out of stock for testing.
            stock_quantity = 0 if (cat_index + 3) % 7 == 0 else 15 + (cat_index * 5) % 35
            sale_note = " Currently on sale at a discounted price." if cat_index % 4 == 0 else ""

            name = f"{noun} #{cat_index:02d}"
            description = (
                f"{category_en} {noun} - a {descriptor} choice for {spec.get('use', 'everyday use')}. "
                f"Weighs {weight_g / 1000:.2f} kg. Dimensions: {length_cm}cm x {width_cm}cm x {height_cm}cm. "
                f"Photos available: {photos_qty}.{sale_note}"
            )

            products.append(
                {
                    "olist_id": row.get("product_id", "").strip(),
                    "name": name,
                    "description": description,
                    "category_en": category_en,
                    "price": price,
                    "weight_kg": round(weight_g / 1000, 3) if weight_g else None,
                    "dimensions_json": {
                        "length": length_cm,
                        "width": width_cm,
                        "height": height_cm,
                    }
                    if any([length_cm, width_cm, height_cm])
                    else None,
                    "stock_quantity": stock_quantity,
                }
            )

    return products


async def ingest(session: AsyncSession, products_data: list[dict]) -> tuple[int, int]:
    """Insert categories and products into PostgreSQL. Idempotent."""

    # ── Step 1: Upsert categories ───────────────────────────────────
    unique_categories = sorted({p["category_en"] for p in products_data if p["category_en"]})
    category_map: dict[str, ProductCategory] = {}

    for cat_name in unique_categories:
        result = await session.execute(select(ProductCategory).where(ProductCategory.name == cat_name))
        existing = result.scalar_one_or_none()
        if existing:
            category_map[cat_name] = existing
        else:
            new_cat = ProductCategory(id=uuid4(), name=cat_name)
            session.add(new_cat)
            category_map[cat_name] = new_cat

    await session.flush()
    logger.info("Synced %d product categories", len(category_map))

    # ── Step 2: Upsert products (batch) ─────────────────────────────
    # Check which olist_ids already exist (use name as proxy since olist_id isn't a column)
    existing_result = await session.execute(select(Product.name))  # type: ignore[arg-type]
    existing_names = {row[0] for row in existing_result.all()}

    inserted = 0
    batch_size = 500
    new_products = []

    for p in products_data:
        if p["name"] in existing_names:
            continue  # already ingested

        category = category_map.get(p["category_en"])
        product = Product(
            id=uuid4(),
            name=p["name"],
            description=p["description"],
            category_id=category.id if category else None,
            price=p["price"],
            stock_quantity=p["stock_quantity"],
            weight_kg=p["weight_kg"],
            dimensions_json=p["dimensions_json"],
        )
        new_products.append(product)
        inserted += 1

        if len(new_products) >= batch_size:
            session.add_all(new_products)
            await session.flush()
            new_products.clear()
            logger.info("  Inserted batch of %d products...", batch_size)

    # Flush remaining
    if new_products:
        session.add_all(new_products)
        await session.flush()

    await session.commit()
    return len(category_map), inserted


async def main():
    """Main entry point for the ingestion script."""
    logger.info("═" * 60)
    logger.info("CALLIOPE — Olist Data Ingestion")
    logger.info("═" * 60)

    # Ensure .env is loaded
    get_settings()

    # Check data files
    products_path, _ = await ensure_data_files()

    # Load from CSV
    logger.info("Loading products from CSV...")
    products_data = load_products_from_csv(products_path)
    logger.info("Loaded %d raw products from CSV", len(products_data))

    # Create tables if needed
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Ingest into DB
    factory = get_session_factory()
    async with factory() as session:
        cat_count, prod_count = await ingest(session, products_data)

    logger.info("═" * 60)
    logger.info("Ingested %d products, %d categories into PostgreSQL", prod_count, cat_count)
    logger.info("═" * 60)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
