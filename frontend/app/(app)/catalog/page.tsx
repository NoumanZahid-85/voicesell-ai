"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
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
  X,
  Barcode,
  Scales,
  Ruler,
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
  const [detail, setDetail] = useState<Product | null>(null);

  // Deep link: /catalog?category=<uuid> (Admin console drills through here).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const cat = params.get("category");
    if (cat) setActiveCategory(cat);
    const q = params.get("search");
    if (q) setSearch(q);
  }, []);

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
            background: "var(--ink-1)",
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
          <div style={{ display: "none", borderBottom: "1px solid var(--line)", background: "var(--ink-1)" }} data-catalog-rail-mobile>
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
              background: "var(--ink-1)",
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
                    onClick={() => setDetail(p)}
                    role="button"
                    tabIndex={0}
                    aria-label={`View details for ${p.name}`}
                    onKeyDown={(e) => { if (e.key === "Enter") setDetail(p); }}
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

      {/* ── Product detail modal ── */}
      <AnimatePresence>
        {detail && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            onClick={() => setDetail(null)}
            style={{
              position: "fixed",
              inset: 0,
              zIndex: 60,
              background: "rgba(10, 10, 16, 0.55)",
              backdropFilter: "blur(3px)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: 20,
            }}
            role="dialog"
            aria-modal="true"
            aria-label={`${detail.name} details`}
          >
            <motion.div
              initial={{ opacity: 0, y: 16, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.98 }}
              transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
              onClick={(e) => e.stopPropagation()}
              className="panel"
              style={{
                width: "min(560px, 100%)",
                maxHeight: "85dvh",
                overflow: "auto",
                padding: 0,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "14px 22px",
                  borderBottom: "1px solid var(--line)",
                  background: "var(--ink-1)",
                  position: "sticky",
                  top: 0,
                }}
              >
                <span className="label-mono" style={{ textTransform: "none", letterSpacing: "0.08em" }}>
                  {categories.find((c) => c.id === detail.category_id)?.name ?? "General"}
                </span>
                <button className="btn-ghost btn-sm" onClick={() => setDetail(null)} aria-label="Close details">
                  <X size={14} />
                </button>
              </div>

              <div style={{ padding: "22px 24px" }}>
                <h2 style={{ fontSize: "1.25rem", fontWeight: 700, marginBottom: 6 }}>{detail.name}</h2>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
                  <span className="product-price" style={{ fontSize: "1.15rem" }}>
                    {formatCurrency(detail.price)}
                  </span>
                  <StockBadge stock={detail.stock_quantity} />
                </div>

                {detail.description && (
                  <p style={{ fontSize: "0.9rem", lineHeight: 1.65, color: "var(--text-mid)", marginBottom: 18 }}>
                    {detail.description}
                  </p>
                )}

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                    gap: 10,
                  }}
                >
                  <div className="panel" style={{ padding: "12px 14px" }}>
                    <div className="label-mono" style={{ fontSize: "0.6rem", marginBottom: 4 }}>
                      <Barcode size={11} style={{ verticalAlign: -1 }} /> PRODUCT ID
                    </div>
                    <code className="mono-id" style={{ fontSize: "0.72rem", wordBreak: "break-all" }}>
                      {detail.id}
                    </code>
                  </div>
                  {detail.weight_kg != null && (
                    <div className="panel" style={{ padding: "12px 14px" }}>
                      <div className="label-mono" style={{ fontSize: "0.6rem", marginBottom: 4 }}>
                        <Scales size={11} style={{ verticalAlign: -1 }} /> WEIGHT
                      </div>
                      <div style={{ fontWeight: 650, fontSize: "0.88rem" }}>{detail.weight_kg} kg</div>
                    </div>
                  )}
                  {detail.dimensions_json && (detail.dimensions_json.length || detail.dimensions_json.width || detail.dimensions_json.height) ? (
                    <div className="panel" style={{ padding: "12px 14px" }}>
                      <div className="label-mono" style={{ fontSize: "0.6rem", marginBottom: 4 }}>
                        <Ruler size={11} style={{ verticalAlign: -1 }} /> DIMENSIONS
                      </div>
                      <div style={{ fontWeight: 650, fontSize: "0.88rem" }}>
                        {detail.dimensions_json.length ?? "–"} × {detail.dimensions_json.width ?? "–"} ×{" "}
                        {detail.dimensions_json.height ?? "–"} cm
                      </div>
                    </div>
                  ) : null}
                  <div className="panel" style={{ padding: "12px 14px" }}>
                    <div className="label-mono" style={{ fontSize: "0.6rem", marginBottom: 4 }}>
                      UNITS IN STOCK
                    </div>
                    <div style={{ fontWeight: 650, fontSize: "0.88rem" }}>{detail.stock_quantity}</div>
                  </div>
                </div>

                <p className="mono-id" style={{ fontSize: "0.66rem", marginTop: 16, color: "var(--text-low)" }}>
                  ORDER BY VOICE OR TEXT — SAY “ORDER ONE {detail.name.toUpperCase()}” IN THE CHAT
                </p>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
