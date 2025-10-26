CREATE DATABASE cinema_swiper;
USE cinema_swiper;

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) UNIQUE
);

CREATE TABLE favorites (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    title VARCHAR(255),
    poster VARCHAR(255),
    url VARCHAR(255),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
