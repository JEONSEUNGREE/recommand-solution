package com.example.recommend.sync;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public class RvProductRepository {

    private final JdbcTemplate jdbc;

    public RvProductRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private static final RowMapper<RvProduct> ROW = (rs, rn) -> new RvProduct(
            rs.getLong("id"),
            rs.getLong("advertiser_id"),
            rs.getString("product_code"),
            rs.getString("product_origin_code"),
            rs.getString("product_name"),
            rs.getString("company_nm"),
            (BigDecimal) rs.getObject("price"),
            (BigDecimal) rs.getObject("sale_price"),
            rs.getString("product_url"),
            rs.getString("image_url"),
            rs.getString("body_text"),
            (Integer) rs.getObject("body_text_len"),
            rs.getString("html_path"),
            rs.getString("image_dir"),
            (Integer) rs.getObject("image_local_count"),
            rs.getString("scrape_status"),
            rs.getObject("scraped_at", OffsetDateTime.class),
            rs.getString("scrape_error"),
            rs.getString("enrich_method"),
            rs.getString("enriched_info"),
            rs.getObject("enriched_at", OffsetDateTime.class),
            rs.getString("enrich_error")
    );

    public List<RvProduct> page(long advertiserId, int limit, int offset, String filter) {
        // filter: null|"" → all, "to_scrape" → 미스크랩, "to_enrich" → 스크랩됐지만 enrich 안 됨, "done" → enrich 완료
        StringBuilder sql = new StringBuilder("SELECT * FROM rv_products WHERE advertiser_id = ?");
        if ("to_scrape".equals(filter)) sql.append(" AND scrape_status IS NULL");
        else if ("to_enrich".equals(filter)) sql.append(" AND scrape_status = 'scraped' AND enrich_method IS NULL");
        else if ("done".equals(filter)) sql.append(" AND enrich_method IS NOT NULL");
        sql.append(" ORDER BY id ASC LIMIT ? OFFSET ?");
        return jdbc.query(sql.toString(), ROW, advertiserId, limit, offset);
    }

    public long count(long advertiserId, String filter) {
        StringBuilder sql = new StringBuilder("SELECT COUNT(*) FROM rv_products WHERE advertiser_id = ?");
        if ("to_scrape".equals(filter)) sql.append(" AND scrape_status IS NULL");
        else if ("to_enrich".equals(filter)) sql.append(" AND scrape_status = 'scraped' AND enrich_method IS NULL");
        else if ("done".equals(filter)) sql.append(" AND enrich_method IS NOT NULL");
        Long n = jdbc.queryForObject(sql.toString(), Long.class, advertiserId);
        return n == null ? 0L : n;
    }

    public Optional<RvProduct> findOne(long advertiserId, String productCode) {
        return jdbc.query(
                "SELECT * FROM rv_products WHERE advertiser_id = ? AND product_code = ?",
                ROW, advertiserId, productCode
        ).stream().findFirst();
    }

    public void updateAfterScrape(long advertiserId, String productCode,
                                   String bodyText, int bodyTextLen,
                                   String htmlPath, String imageDir, int imageLocalCount,
                                   String status, String error) {
        jdbc.update(
                "UPDATE rv_products SET body_text = ?, body_text_len = ?, html_path = ?, " +
                        "image_dir = ?, image_local_count = ?, scrape_status = ?, scraped_at = NOW(), " +
                        "scrape_error = ?, mod_date = NOW() " +
                        "WHERE advertiser_id = ? AND product_code = ?",
                bodyText, bodyTextLen, htmlPath, imageDir, imageLocalCount, status, error,
                advertiserId, productCode
        );
    }

    public void updateAfterEnrich(long advertiserId, String productCode,
                                   String method, String enrichedInfo, String error) {
        jdbc.update(
                "UPDATE rv_products SET enrich_method = ?, enriched_info = ?, enriched_at = NOW(), " +
                        "enrich_error = ?, mod_date = NOW() " +
                        "WHERE advertiser_id = ? AND product_code = ?",
                method, enrichedInfo, error, advertiserId, productCode
        );
    }
}
