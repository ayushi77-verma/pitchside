USE pitchside;

-- 1) Players (5 per team for now; you can add more later)
INSERT INTO players (team_id, full_name, role, jersey_no) VALUES
 (1,'Aarav Mehta','Batter',7),(1,'Rohan Singh','Bowler',18),(1,'Kabir Jain','All-rounder',45),(1,'Dev Sharma','Wicketkeeper',1),(1,'Ishaan Gupta','Bowler',10),
 (2,'Vihaan Rao','Batter',9),(2,'Arjun Nair','Bowler',11),(2,'Neel Shah','All-rounder',33),(2,'Krish Verma','Wicketkeeper',2),(2,'Yash Kapoor','Bowler',14),
 (3,'Reyansh Joshi','Batter',5),(3,'Aditya Das','Bowler',22),(3,'Samar Khan','All-rounder',8),(3,'Tanay Bose','Wicketkeeper',3),(3,'Parth Iyer','Bowler',19),
 (4,'Dhruv Malhotra','Batter',17),(4,'Karan Sethi','Bowler',24),(4,'Om Pandey','All-rounder',27),(4,'Veer Chauhan','Wicketkeeper',4),(4,'Manav Reddy','Bowler',12);

-- 2) A log table for the trigger to write into
CREATE TABLE match_log (
  log_id INT AUTO_INCREMENT PRIMARY KEY,
  match_id INT NOT NULL,
  note VARCHAR(120) NOT NULL,
  logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3) VIEW: the points table, ready to display
CREATE VIEW v_points_standings AS
SELECT t.team_name, p.played, p.won, p.lost, p.points,
       ROUND(CASE WHEN p.played = 0 THEN 0 ELSE p.won * 100 / p.played END, 1) AS win_percent
FROM points_table p JOIN teams t ON t.team_id = p.team_id
ORDER BY p.points DESC, p.won DESC;

DELIMITER $$

-- 4) TRIGGER: when a match becomes Completed, update points automatically
CREATE TRIGGER trg_match_completed
AFTER UPDATE ON matches
FOR EACH ROW
BEGIN
  IF NEW.status = 'Completed' AND OLD.status <> 'Completed'
     AND NEW.winner_team_id IS NOT NULL THEN
    UPDATE points_table SET played = played + 1
      WHERE team_id IN (NEW.team1_id, NEW.team2_id);
    UPDATE points_table SET won = won + 1, points = points + 2
      WHERE team_id = NEW.winner_team_id;
    UPDATE points_table SET lost = lost + 1
      WHERE team_id = IF(NEW.winner_team_id = NEW.team1_id, NEW.team2_id, NEW.team1_id);
    INSERT INTO match_log (match_id, note)
      VALUES (NEW.match_id, CONCAT('Completed. Winner team id ', NEW.winner_team_id));
  END IF;
END$$

-- 5) STORED PROCEDURE with a TRANSACTION
CREATE PROCEDURE complete_match(IN p_match_id INT, IN p_winner_id INT)
BEGIN
  DECLARE v_status VARCHAR(20);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    RESIGNAL;
  END;

  START TRANSACTION;
  SELECT status INTO v_status FROM matches WHERE match_id = p_match_id FOR UPDATE;
  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Match not found';
  ELSEIF v_status = 'Completed' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Match already completed';
  END IF;
  UPDATE matches SET status = 'Completed', winner_team_id = p_winner_id
    WHERE match_id = p_match_id;
  COMMIT;
END$$

DELIMITER ;