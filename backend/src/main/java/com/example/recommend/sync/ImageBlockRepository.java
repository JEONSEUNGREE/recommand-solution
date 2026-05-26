package com.example.recommend.sync;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;

@Repository
public class ImageBlockRepository {

    private final JdbcTemplate jdbc;

    public ImageBlockRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private static final RowMapper<ImageBlock> ROW = (rs, rn) -> new ImageBlock(
            rs.getLong("id"),
            rs.getLong("advertiser_id"),
            rs.getString("pattern"),
            (Boolean) rs.getObject("enabled"),
            rs.getString("notes"),
            rs.getObject("created_at", OffsetDateTime.class),
            rs.getObject("updated_at", OffsetDateTime.class)
    );

    public List<ImageBlock> findAll(long advertiserId) {
        return jdbc.query(
                "SELECT * FROM advertiser_image_blocks WHERE advertiser_id=? ORDER BY id",
                ROW, advertiserId
        );
    }

    public List<String> findEnabledPatterns(long advertiserId) {
        return jdbc.queryForList(
                "SELECT pattern FROM advertiser_image_blocks WHERE advertiser_id=? AND enabled=TRUE",
                String.class, advertiserId
        );
    }

    public void delete(long id) {
        jdbc.update("DELETE FROM advertiser_image_blocks WHERE id=?", id);
    }

    public void replaceAll(long advertiserId, List<ImageBlock> items) {
        jdbc.update("DELETE FROM advertiser_image_blocks WHERE advertiser_id=?", advertiserId);
        for (ImageBlock b : items) {
            if (b.pattern() == null || b.pattern().isBlank()) continue;
            jdbc.update(
                    "INSERT INTO advertiser_image_blocks (advertiser_id, pattern, enabled, notes) VALUES (?, ?, ?, ?)",
                    advertiserId, b.pattern(),
                    b.enabled() == null ? Boolean.TRUE : b.enabled(),
                    b.notes()
            );
        }
    }
}
