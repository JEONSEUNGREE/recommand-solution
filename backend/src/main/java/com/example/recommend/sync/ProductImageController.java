package com.example.recommend.sync;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;

@RestController
@RequestMapping("/advertisers/{advertiserId}/products/{productCode}/images")
public class ProductImageController {

    private static final Path DATA_DIR = Path.of(
            System.getenv().getOrDefault("RECOMMAND_DATA_DIR", "D:/recommand-data")
    );

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper = new ObjectMapper();

    public ProductImageController(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private Path imageDir(long advertiserId, String code) {
        String safe = code.replaceAll("[^\\w\\-.]", "_");
        return DATA_DIR.resolve("advertisers").resolve(String.valueOf(advertiserId))
                .resolve("products").resolve(safe).resolve("images");
    }

    private Path productDir(long advertiserId, String code) {
        String safe = code.replaceAll("[^\\w\\-.]", "_");
        return DATA_DIR.resolve("advertisers").resolve(String.valueOf(advertiserId))
                .resolve("products").resolve(safe);
    }

    public record ImageMeta(String filename, long size, boolean excluded, String src) {}

    @GetMapping
    public List<ImageMeta> list(@PathVariable long advertiserId, @PathVariable String productCode) throws IOException {
        Path dir = imageDir(advertiserId, productCode);
        if (!Files.exists(dir)) return List.of();

        Set<String> excluded = new HashSet<>(jdbc.queryForList(
                "SELECT image_filename FROM rv_product_excluded_images WHERE advertiser_id=? AND product_code=?",
                String.class, advertiserId, productCode
        ));

        // extracted.json에서 filename → src URL 매핑 만들기
        Map<String, String> filenameToSrc = new HashMap<>();
        Path extracted = productDir(advertiserId, productCode).resolve("extracted.json");
        if (Files.exists(extracted)) {
            try {
                JsonNode root = mapper.readTree(Files.readString(extracted));
                JsonNode arr = root.path("image_files");
                if (arr.isArray()) {
                    for (JsonNode n : arr) {
                        String src = n.path("src").asText("");
                        String localPath = n.path("local_path").asText("");
                        if (!src.isEmpty() && !localPath.isEmpty()) {
                            String fname = Path.of(localPath).getFileName().toString();
                            filenameToSrc.put(fname, src);
                        }
                    }
                }
            } catch (Exception ignored) {}
        }

        List<ImageMeta> out = new ArrayList<>();
        try (var stream = Files.list(dir)) {
            stream.sorted().forEach(p -> {
                try {
                    String name = p.getFileName().toString();
                    out.add(new ImageMeta(
                            name,
                            Files.size(p),
                            excluded.contains(name),
                            filenameToSrc.getOrDefault(name, "")
                    ));
                } catch (IOException ignored) {}
            });
        }
        return out;
    }

    @GetMapping("/{filename}")
    public ResponseEntity<Resource> serve(@PathVariable long advertiserId,
                                            @PathVariable String productCode,
                                            @PathVariable String filename) {
        Path p = imageDir(advertiserId, productCode).resolve(filename).normalize();
        if (!p.startsWith(imageDir(advertiserId, productCode)) || !Files.exists(p)) {
            return ResponseEntity.notFound().build();
        }
        String ext = filename.toLowerCase();
        MediaType mt = ext.endsWith(".png") ? MediaType.IMAGE_PNG
                : ext.endsWith(".gif") ? MediaType.IMAGE_GIF
                : MediaType.IMAGE_JPEG;
        return ResponseEntity.ok().contentType(mt).body(new FileSystemResource(p));
    }

    @PostMapping("/{filename}/exclude")
    public ResponseEntity<Void> exclude(@PathVariable long advertiserId,
                                          @PathVariable String productCode,
                                          @PathVariable String filename,
                                          @RequestBody(required = false) Map<String, String> body) {
        String reason = body == null ? null : body.get("reason");
        jdbc.update(
                "INSERT INTO rv_product_excluded_images (advertiser_id, product_code, image_filename, reason) " +
                        "VALUES (?, ?, ?, ?) ON CONFLICT (advertiser_id, product_code, image_filename) DO NOTHING",
                advertiserId, productCode, filename, reason
        );
        return ResponseEntity.noContent().build();
    }

    @DeleteMapping("/{filename}/exclude")
    public ResponseEntity<Void> unexclude(@PathVariable long advertiserId,
                                            @PathVariable String productCode,
                                            @PathVariable String filename) {
        jdbc.update(
                "DELETE FROM rv_product_excluded_images WHERE advertiser_id=? AND product_code=? AND image_filename=?",
                advertiserId, productCode, filename
        );
        return ResponseEntity.noContent().build();
    }
}
