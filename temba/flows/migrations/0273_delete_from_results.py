# Generated by Django 4.0.2 on 2022-02-22 21:41

from django.db import migrations

SQL = """
----------------------------------------------------------------------
-- Handles insertion of flow runs
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_flowrun_insert() RETURNS TRIGGER AS $$
DECLARE
    p INT;
    _path_json JSONB;
    _path_len INT;
BEGIN
    -- if this run is part of a flow start, increment that start's count of runs
    IF NEW.start_id IS NOT NULL THEN
        PERFORM temba_insert_flowstartcount(NEW.start_id, 1);
    END IF;

    -- increment node count at current node in this path if this is an active run
    IF NEW.status IN ('A', 'W') AND NEW.current_node_uuid IS NOT NULL THEN
        PERFORM temba_insert_flownodecount(NEW.flow_id, NEW.current_node_uuid, 1);
    END IF;

    -- nothing more to do if path is empty
    IF NEW.path IS NULL OR NEW.path = '[]' THEN RETURN NULL; END IF;

    -- parse path as JSON
    _path_json := NEW.path::json;
    _path_len := jsonb_array_length(_path_json);

    -- for each step in the path, increment the path count, and record a recent run
    p := 1;
    LOOP
        EXIT WHEN p >= _path_len;

        PERFORM temba_insert_flowpathcount(
            NEW.flow_id,
            UUID(_path_json->(p-1)->>'exit_uuid'),
            UUID(_path_json->p->>'node_uuid'),
            timestamptz(_path_json->p->>'arrived_on'),
            1
        );
        p := p + 1;
    END LOOP;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles deletion of flow runs
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_flowrun_delete() RETURNS TRIGGER AS $$
DECLARE
    p INT;
    _path_json JSONB;
    _path_len INT;
BEGIN
    -- if we're deleting a run which is sitting at a node, decrement that node's count
    IF OLD.status IN ('A', 'W') AND OLD.current_node_uuid IS NOT NULL THEN
        PERFORM temba_insert_flownodecount(OLD.flow_id, OLD.current_node_uuid, -1);
    END IF;

    -- if this is a user delete then remove from results
    IF OLD.delete_from_results THEN
        PERFORM temba_insert_flowruncount(OLD.flow_id, OLD.exit_type, -1);
        PERFORM temba_update_category_counts(OLD.flow_id, NULL, OLD.results::json);

        -- nothing more to do if path was empty
        IF OLD.path IS NULL OR OLD.path = '[]' THEN RETURN NULL; END IF;

        -- parse path as JSON
        _path_json := OLD.path::json;
        _path_len := jsonb_array_length(_path_json);

        -- for each step in the path, decrement the path count
        p := 1;
        LOOP
            EXIT WHEN p >= _path_len;

            -- it's possible that steps from old flows don't have exit_uuid
            IF (_path_json->(p-1)->'exit_uuid') IS NOT NULL THEN
                PERFORM temba_insert_flowpathcount(
                    OLD.flow_id,
                    UUID(_path_json->(p-1)->>'exit_uuid'),
                    UUID(_path_json->p->>'node_uuid'),
                    timestamptz(_path_json->p->>'arrived_on'),
                    -1
                );
            END IF;

            p := p + 1;
        END LOOP;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles changes to a run's results
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_update_flowcategorycount() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        EXECUTE temba_update_category_counts(NEW.flow_id, NEW.results::json, NULL);
    ELSIF TG_OP = 'UPDATE' THEN
        -- use string comparison to check for no-change case
        IF NEW.results = OLD.results THEN RETURN NULL; END IF;

        EXECUTE temba_update_category_counts(NEW.flow_id, NEW.results::json, OLD.results::json);
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles changes to a run's exit_type
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_update_flowruncount() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM temba_insert_flowruncount(NEW.flow_id, NEW.exit_type, 1);
    ELSIF TG_OP = 'UPDATE' THEN
        PERFORM temba_insert_flowruncount(OLD.flow_id, OLD.exit_type, -1);
        PERFORM temba_insert_flowruncount(NEW.flow_id, NEW.exit_type, 1);
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER temba_flowrun_update_flowcategorycount ON flows_flowrun;
CREATE TRIGGER temba_flowrun_update_flowcategorycount
    AFTER INSERT OR UPDATE OF results ON flows_flowrun
    FOR EACH ROW EXECUTE PROCEDURE temba_update_flowcategorycount();

DROP TRIGGER temba_flowrun_update_flowruncount ON flows_flowrun;
CREATE TRIGGER temba_flowrun_update_flowruncount
    AFTER INSERT OR UPDATE OF exit_type ON flows_flowrun
    FOR EACH ROW EXECUTE PROCEDURE temba_update_flowruncount();

DROP TRIGGER temba_flowrun_update_flowstartcount ON flows_flowrun;
DROP FUNCTION temba_update_flowstartcount();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("flows", "0272_flowrun_delete_from_counts"),
    ]

    operations = [migrations.RunSQL(SQL)]
