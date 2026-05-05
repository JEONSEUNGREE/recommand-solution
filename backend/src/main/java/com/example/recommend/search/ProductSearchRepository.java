package com.example.recommend.search;

import com.pgvector.PGvector;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** 필터 + 벡터 검색을 한 SQL로 실행. pgvector 코사인 거리 사용. */
@Repository
public class ProductSearchRepository {

    private final JdbcTemplate jdbc;

    public ProductSearchRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public List<Product> search(Map<String, Object> filters, float[] queryVector, int limit) {
        List<String> where = new ArrayList<>();
        List<Object> params = new ArrayList<>();

        where.add("p.deleted_at IS NULL");

        boolean inStockOnly = !Boolean.FALSE.equals(filters.get("in_stock_only"));
        if (inStockOnly) where.add("p.stock > 0");

        if (filters.get("category") instanceof String s) {
            where.add("p.category = ?"); params.add(s);
        }
        if (filters.get("gender") instanceof String s) {
            where.add("p.gender IN (?, '공용')"); params.add(s);
        }
        if (filters.get("season") instanceof String s) {
            where.add("p.season IN (?, '사계절')"); params.add(s);
        }
        if (filters.get("style") instanceof String s) {
            where.add("p.style = ?"); params.add(s);
        }
        if (filters.get("color") instanceof String s) {
            where.add("p.color = ?"); params.add(s);
        }
        if (filters.get("price_min") instanceof Number n) {
            where.add("COALESCE(p.sale_price, p.price) >= ?"); params.add(n.intValue());
        }
        if (filters.get("price_max") instanceof Number n) {
            where.add("COALESCE(p.sale_price, p.price) <= ?"); params.add(n.intValue());
        }

        // SELECT은 거리 비교를 위해 vec 파라미터 한 번, ORDER BY에 또 한 번
        PGvector vec = new PGvector(queryVector);
        String sql = """
                SELECT p.id, p.sku, p.name, p.brand, p.category, p.subcategory,
                       p.gender, p.season, p.style, p.color, p.price, p.sale_price,
                       p.stock, p.description,
                       (e.embedding <=> ?) AS distance
                FROM products p
                JOIN product_embeddings e ON e.product_id = p.id
                WHERE %s
                ORDER BY e.embedding <=> ?
                LIMIT ?
                """.formatted(String.join(" AND ", where));

        List<Object> finalParams = new ArrayList<>();
        finalParams.add(vec);
        finalParams.addAll(params);
        finalParams.add(vec);
        finalParams.add(limit);

        return jdbc.query(sql, (rs, rowNum) -> new Product(
                rs.getLong("id"),
                rs.getString("sku"),
                rs.getString("name"),
                rs.getString("brand"),
                rs.getString("category"),
                rs.getString("subcategory"),
                rs.getString("gender"),
                rs.getString("season"),
                rs.getString("style"),
                rs.getString("color"),
                (Integer) rs.getObject("price"),
                (Integer) rs.getObject("sale_price"),
                (Integer) rs.getObject("stock"),
                rs.getString("description"),
                rs.getDouble("distance")
        ), finalParams.toArray());
    }
}
