USE pitchside;

CREATE TABLE teams (
  team_id INT AUTO_INCREMENT PRIMARY KEY,
  team_name VARCHAR(60) NOT NULL UNIQUE,
  short_code CHAR(3) NOT NULL UNIQUE,
  college VARCHAR(80) NOT NULL
);

CREATE TABLE players (
  player_id INT AUTO_INCREMENT PRIMARY KEY,
  team_id INT NOT NULL,
  full_name VARCHAR(80) NOT NULL,
  role ENUM('Batter','Bowler','All-rounder','Wicketkeeper') NOT NULL,
  jersey_no TINYINT NOT NULL,
  CONSTRAINT fk_player_team FOREIGN KEY (team_id) REFERENCES teams(team_id),
  CONSTRAINT uq_team_jersey UNIQUE (team_id, jersey_no),
  CONSTRAINT chk_jersey CHECK (jersey_no BETWEEN 1 AND 99)
);

CREATE TABLE venues (
  venue_id INT AUTO_INCREMENT PRIMARY KEY,
  venue_name VARCHAR(80) NOT NULL UNIQUE,
  city VARCHAR(50) NOT NULL
);

CREATE TABLE matches (
  match_id INT AUTO_INCREMENT PRIMARY KEY,
  team1_id INT NOT NULL,
  team2_id INT NOT NULL,
  venue_id INT NOT NULL,
  match_date DATE NOT NULL,
  overs TINYINT NOT NULL DEFAULT 10,
  status ENUM('Scheduled','Live','Completed') NOT NULL DEFAULT 'Scheduled',
  winner_team_id INT NULL,
  CONSTRAINT fk_m_team1 FOREIGN KEY (team1_id) REFERENCES teams(team_id),
  CONSTRAINT fk_m_team2 FOREIGN KEY (team2_id) REFERENCES teams(team_id),
  CONSTRAINT fk_m_venue FOREIGN KEY (venue_id) REFERENCES venues(venue_id),
  CONSTRAINT fk_m_winner FOREIGN KEY (winner_team_id) REFERENCES teams(team_id),
  CONSTRAINT chk_diff_teams CHECK (team1_id <> team2_id),
  CONSTRAINT chk_overs CHECK (overs BETWEEN 1 AND 50),
  CONSTRAINT chk_winner CHECK (winner_team_id IS NULL OR winner_team_id IN (team1_id, team2_id))
);

CREATE TABLE points_table (
  team_id INT PRIMARY KEY,
  played INT NOT NULL DEFAULT 0,
  won INT NOT NULL DEFAULT 0,
  lost INT NOT NULL DEFAULT 0,
  points INT NOT NULL DEFAULT 0,
  CONSTRAINT fk_points_team FOREIGN KEY (team_id) REFERENCES teams(team_id)
);

CREATE INDEX idx_matches_date_status ON matches(match_date, status);

INSERT INTO teams (team_name, short_code, college) VALUES
 ('Jaipur Jaguars','JJG','Manipal University Jaipur'),
 ('Pink City Panthers','PCP','Manipal University Jaipur'),
 ('Desert Falcons','DFL','Manipal University Jaipur'),
 ('Aravali Titans','ATN','Manipal University Jaipur');
INSERT INTO points_table (team_id) SELECT team_id FROM teams;
INSERT INTO venues (venue_name, city) VALUES ('MUJ Main Ground','Jaipur');