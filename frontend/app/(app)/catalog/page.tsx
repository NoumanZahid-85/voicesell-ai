"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";
import {
  ArrowClockwise,
  Armchair,
  BeachBall,
  Bed,
  Car,
  Clock,
  CookingPot,
  Desktop,
  DeviceMobile,
  Barbell,
  FlowerLotus,
  Gift,
  MagnifyingGlass,
  ShoppingBagOpen,
  Sparkle,
  Storefront,
  WarningCircle,
} from "@phosphor-icons/react";
import { api } from "@/lib/api";
import type { Category, Product } from "@/lib/types";
import {
  PageHeader,
  StockBadge,
  LoadingRows,
  ErrorPanel,
  EmptyPanel,
} from "@/app/components/ui";
import { formatCurrency } from "@/lib/format";

/** Category → tuner key icon. Unknown categories fall back to Storefront. */
const CATEGORY_ICONS: Record<string, React.ReactNode> = {
  Automotive: <Car size={15} weight="duotone" />,
  "Bed, Bath & Table": <Bed size={15} weight="duotone" />,
  "Computer Accessories": <Desktop size={15} weight="duotone" />,
  "Furniture & Decor": <Armchair size={15} weight="duotone" />,
  "Health & Beauty": <Sparkle size={15} weight="duotone" />,
  Housewares: <CookingPot size={15} weight="duotone" />,
  Perfumery: <FlowerLotus size={15} weight="duotone" />,
  "Sports & Leisure": <Barbell size={15} weight="duotone" />,
  Telephony: <DeviceMobile size={15} weight="duotone" />,
  "Watches & Gifts": <Gift size={15} weight="duotone" />,
  "Beach & Outdoor": <BeachBall size={15} weight="duotone" />,
};

function categoryIcon(name: string): React.ReactNode {
  return CATEGORY_ICONS[name] ?? <Storefront size={15} weight="duotone" />;
}

export default function CatalogPage() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [onlyLowStock, setOnlyLowStock] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .categories()
      .then((data) => {
        if (!cancelled) setCategories(data.categories);
      })
      .catch(() => {
        // The rail renders from products alone if categories fail.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const fetchProducts = useCallback(async (q: string, categoryId: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.products({
        search: q,
        categoryId: categoryId ?? undefined,
      });
      setProducts(data.products);
      setTotal(data.total ?? 0);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setProducts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => fetchProducts(search, activeCategory), 250);
    return () => clearTimeout(t);
  }, [search, activeCategory, fetchProducts]);

  const activeName = useMemo(() => {
    if (!activeCategory) return "ALL DEPARTMENTS";
    return (categories.find((c) => c.id === activeCategory)?.name ?? "CATALOG").toUpperCase();
  }, [activeCategory, categories]);

  const inventoryValue = products.reduce((s, p) => s + p.price * p.stock_quantity, 0);
  const lowStock = products.filter((p) => p.stock_quantity < 10);
  const shown = onlyLowStock ? lowStock : products;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100dvh", minWidth: 0 }}>
      {/* ── Header ── */}
      <PageHeader
        icon={<ShoppingBagOpen size={17} weight="duotone" />}
        title="Product Catalog"
        subtitle={`TUNED TO ${activeName} · ${total} PRODUCTS · ${formatCurrency(inventoryValue)} INVENTORY`}
        actions={
          <button className="btn-ghost btn-sm" onClick={() => fetchProducts(search, activeCategory)}>
            <ArrowClockwise size={13} /> Refresh
          </button>
        }
      />

      {/* ── Body: tuner rail + grid ── */}
      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        {/* Desktop rail */}
        <aside
          className="scroll-area"
          style={{
            display: "none",
            width: 236,
            flexShrink: 0,
            borderRight: "1px solid var(--line)",
            background: "rgba(15, 18, 24, 0.4)",
          }}
          data-catalog-rail
        >
          <div className="cat-rail">
            <button
              className="cat-key"
              aria-pressed={!activeCategory}
              onClick={() => setActiveCategory(null)}
            >
              <Storefront size={15} weight="duotone" />
              All departments
              <span className="cat-count">{categories.reduce((s, c) => s + c.product_count, 0)}</span>
            </button>
            {categories.map((c) => (
              <button
                key={c.id}
                className="cat-key"
                aria-pressed={activeCategory === c.id}
                onClick={() => setActiveCategory(activeCategory === c.id ? null : c.id)}
              >
                {categoryIcon(c.name)}
                {c.name}
                <span className="cat-count">{c.product_count}</span>
              </button>
            ))}
          </div>
        </aside>

        {/* Content column */}
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          {/* Mobile rail (horizontal scroll) */}
          <div style={{ display: "none", borderBottom: "1px solid var(--line)", background: "rgba(10,12,16,0.4)" }} data-catalog-rail-mobile>
            <div className="cat-rail" style={{ flexDirection: "row", overflowX: "auto", padding: "8px 12px" }}>
              <button
                className="cat-key"
                aria-pressed={!activeCategory}
                onClick={() => setActiveCategory(null)}
                style={{ width: "auto", whiteSpace: "nowrap" }}
              >
                All
                <span className="cat-count">{categories.reduce((s, c) => s + c.product_count, 0)}</span>
              </button>
              {categories.map((c) => (
                <button
                  key={c.id}
                  className="cat-key"
                  aria-pressed={activeCategory === c.id}
                  onClick={() => setActiveCategory(activeCategory === c.id ? null : c.id)}
                  style={{ width: "auto", whiteSpace: "nowrap" }}
                >
                  {categoryIcon(c.name)}
                  {c.name}
                  <span className="cat-count">{c.product_count}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Filters */}
          <div
            style={{
              padding: "11px clamp(16px, 4vw, 28px)",
              borderBottom: "1px solid var(--line)",
              display: "flex",
              alignItems: "center",
              gap: 12,
              flexWrap: "wrap",
              flexShrink: 0,
              background: "rgba(10, 12, 16, 0.4)",
            }}
          >
            <div style={{ position: "relative", flex: "1 1 200px", maxWidth: 300 }}>
              <MagnifyingGlass
                size={14}
                style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "var(--text-low)" }}
              />
              <input
                className="input-field"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search products…"
                style={{ paddingLeft: 32, paddingTop: 8, paddingBottom: 8 }}
                aria-label="Search products"
              />
            </div>
            <button
              className="pill"
              aria-pressed={onlyLowStock}
              onClick={() => setOnlyLowStock(!onlyLowStock)}
            >
              <WarningCircle size={13} />
              Low stock
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.68rem", opacity: 0.8 }}>{lowStock.length}</span>
            </button>
            <span className="chip" style={{ marginLeft: "auto" }}>
              <Clock size={11} weight="fill" />
              {activeName}
            </span>
          </div>

          {/* Grid */}
          <div className="scroll-area" style={{ flex: 1, padding: "18px clamp(16px, 4vw, 28px)" }}>
            {loading ? (
              <LoadingRows count={6} />
            ) : error ? (
              <ErrorPanel message={error} onRetry={() => fetchProducts(search, activeCategory)} />
            ) : shown.length === 0 ? (
              <EmptyPanel
                title={search || activeCategory || onlyLowStock ? "No products match your filters" : "Catalog is empty"}
                message={
                  search || activeCategory || onlyLowStock
                    ? "Try clearing the search, the category, or the low-stock filter."
                    : "Seed the catalog, then re-embed with the products script."
                }
              />
            ) : (
              <motion.div
                key={`${search}|${activeCategory}|${onlyLowStock}`}
                className="product-grid"
                initial="hidden"
                animate="show"
                variants={{
                  hidden: {},
                  show: { transition: { staggerChildren: 0.04, delayChildren: 0.05 } },
                }}
              >
                {shown.map((p) => (
                  <motion.article
                    key={p.id}
                    layout
                    className="panel panel-hover product-card"
                    variants={{
                      hidden: { opacity: 0, y: 12 },
                      show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: [0.16, 1, 0.3, 1] } },
                    }}
                  >
                    <div className="cat-eyebrow">
                      <span className="label-mono" style={{ textTransform: "none", letterSpacing: "0.08em" }}>
                        {categories.find((c) => c.id === p.category_id)?.name ?? "General"}
                      </span>
                      {categoryIcon(categories.find((c) => c.id === p.category_id)?.name ?? "")}
                    </div>
                    <h3 className="product-name">{p.name}</h3>
                    {p.description && <p className="product-desc">{p.description}</p>}
                    <div className="product-foot">
                      <span className="product-price">{formatCurrency(p.price)}</span>
                      <StockBadge stock={p.stock_quantity} />
                    </div>
                  </motion.article>
                ))}
              </motion.div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
