// Central API types — one definition per backend contract.
// The FastAPI response schemas live in backend/app/schemas/; keep these in sync.

export type OrderStatus = "pending" | "confirmed" | "shipped" | "delivered" | "cancelled";

export interface OrderItem {
  id: string;
  product_id: string;
  product_name?: string;
  quantity: number;
  unit_price: number;
  subtotal: number;
}

export interface Order {
  id: string;
  status: OrderStatus;
  total_amount: number;
  created_at: string;
  updated_at: string;
  items?: OrderItem[];
}

export interface OrderListResponse {
  orders: Order[];
  total?: number;
}

export interface Product {
  id: string;
  name: string;
  description?: string;
  category_id?: string | null;
  price: number;
  stock_quantity: number;
  image_url?: string | null;
  weight_kg?: number | null;
  dimensions_json?: { length?: number | null; width?: number | null; height?: number | null } | null;
  created_at?: string;
  updated_at?: string;
}

export interface ProductListResponse {
  products: Product[];
  total: number;
}

export interface Category {
  id: string;
  name: string;
  product_count: number;
}

export interface CategoryListResponse {
  categories: Category[];
  total: number;
}

export interface CategoryInventory {
  id: string;
  name: string;
  product_count: number;
  total_stock: number;
  low_stock_count: number;
}

export interface LowStockItem {
  id: string;
  name: string;
  stock_quantity: number;
  category: string | null;
}

export interface AdminStats {
  orders_total: number;
  orders_active: number;
  orders_cancelled: number;
  revenue_total: number;
  categories: CategoryInventory[];
  low_stock: LowStockItem[];
}

export interface HealthData {
  status: string;
  db?: string;
  qdrant?: string;
}

export interface VoiceSession {
  session_id: string;
  room_name: string;
  age_seconds: number;
  turn_count: number;
}

export interface VoiceConnectResponse {
  session_id: string;
  room_url: string;
}

export interface ChatSource {
  name: string;
  price: number;
  score: number;
}

export interface ChatResponse {
  reply: string;
  sources: ChatSource[];
}
